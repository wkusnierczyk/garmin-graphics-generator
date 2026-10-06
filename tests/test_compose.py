import base64
import io
import json
import urllib.error
from datetime import datetime, timezone

import pytest
from PIL import Image

from garmin_graphics_generator import cli, compose
from garmin_graphics_generator.compose import (
    ApiError,
    ComposeError,
    Gemini,
    Request,
    Truth,
    choose_aspect_ratio,
    fit,
    model_checks,
    read_key,
    read_truths,
    render_prompt,
)

KEY = "test-key-not-real"
CLOCK = lambda: datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)  # noqa: E731


def png_bytes(size=(2520, 1080), colour=(255, 255, 255)):
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


def write_png(path, size=(64, 64), colour=(0, 0, 0)):
    path.write_bytes(png_bytes(size, colour))
    return str(path)


def captures(tmp_path, count=3):
    directory = tmp_path / "captures"
    directory.mkdir(exist_ok=True)
    return [write_png(directory / f"watch-{i}.png") for i in range(1, count + 1)]


def image_response(data=None, mime="image/png", version="gemini-3-pro-image-001"):
    return {
        "modelVersion": version,
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "Here it is."},
                        {
                            "inlineData": {
                                "mimeType": mime,
                                "data": base64.b64encode(data or png_bytes()).decode(
                                    "ascii"
                                ),
                            }
                        },
                    ]
                },
                "finishReason": "STOP",
            }
        ],
    }


def screen_response(answer):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}}]}


class FakeTransport:
    """Records each request and answers from a queue; an exception is raised."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, url, body, key):
        self.requests.append((url, body, key))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def client(*answers, retries=2):
    transport = FakeTransport(*answers)
    sleeps = []
    return (
        Gemini(KEY, transport, retries=retries, sleep=sleeps.append),
        transport,
        sleeps,
    )


class TestAspectRatio:
    def test_a_store_hero_is_cut_from_the_nearest_wider_ratio(self):
        assert choose_aspect_ratio(1440, 720) == "21:9"

    def test_an_offered_ratio_is_taken_exactly(self):
        assert choose_aspect_ratio(1920, 1080) == "16:9"
        assert choose_aspect_ratio(500, 500) == "1:1"

    def test_a_tall_target_takes_the_nearest_ratio_at_least_as_wide(self):
        assert choose_aspect_ratio(720, 1440) == "9:16"

    def test_nothing_is_wide_enough(self):
        with pytest.raises(ComposeError):
            choose_aspect_ratio(1000, 100)


class TestPrompt:
    def test_placeholders_are_filled(self):
        assert (
            render_prompt(
                "$count_word ${count}: $$5", {"count": "5", "count_word": "five"}
            )
            == "five 5: $5"
        )

    def test_a_placeholder_without_a_value_is_an_error(self):
        with pytest.raises(ComposeError, match=r"\$\{roll\}.*--var roll="):
            render_prompt("roll up to $roll", {})

    def test_the_builtins_name_the_count_in_words(self):
        values = compose.builtin_variables(5, 1440, 720)
        assert values == {
            "count": "5",
            "count_word": "five",
            "width": "1440",
            "height": "720",
        }
        assert compose.builtin_variables(40, 1, 1)["count_word"] == "40"


class TestKey:
    def test_from_a_file_trimmed(self, tmp_path):
        path = tmp_path / "key"
        path.write_text(f"  {KEY}\n")
        assert read_key(str(path)) == KEY

    def test_from_the_environment(self):
        assert read_key(environ={"GEMINI_API_KEY": KEY}) == KEY

    def test_the_file_wins_over_the_environment(self, tmp_path):
        path = tmp_path / "key"
        path.write_text("from-file")
        assert read_key(str(path), environ={"GEMINI_API_KEY": KEY}) == "from-file"

    def test_missing_or_empty(self, tmp_path):
        with pytest.raises(ComposeError, match="GEMINI_API_KEY"):
            read_key(environ={})
        empty = tmp_path / "empty"
        empty.write_text("\n")
        with pytest.raises(ComposeError, match="empty"):
            read_key(str(empty))
        with pytest.raises(ComposeError, match="cannot read"):
            read_key(str(tmp_path / "absent"))


class TestTruths:
    def test_reads_a_list(self, tmp_path):
        path = tmp_path / "checks.json"
        path.write_text(
            json.dumps(
                [
                    {"name": "numerals", "question": "Q?", "expect": False},
                    {"name": "one", "question": "R?"},
                ]
            )
        )
        assert read_truths(str(path)) == [
            Truth("numerals", "Q?", False),
            Truth("one", "R?", True),
        ]

    @pytest.mark.parametrize(
        "content",
        [
            "{}",
            '[{"name": "a"}]',
            '[{"name": "a", "question": "q", "expect": "no"}]',
            '[{"name": "a", "question": "q"}, {"name": "a", "question": "r"}]',
            "not json",
        ],
    )
    def test_refuses_a_malformed_file(self, tmp_path, content):
        path = tmp_path / "checks.json"
        path.write_text(content)
        with pytest.raises(ComposeError):
            read_truths(str(path))


class TestFit:
    def test_crops_about_the_centre_then_resizes_exactly(self):
        image = Image.new("RGB", (2100, 900), (255, 0, 0))
        image.paste((0, 0, 255), (300, 0, 1800, 900))
        fitted = fit(image, 1440, 720)
        assert fitted.size == (1440, 720)
        # 21:9 cut to 2:1 loses 150 px from each side, all of it red.
        assert fitted.getpixel((10, 360))[2] < 128
        assert fitted.getpixel((720, 360)) == (0, 0, 255)

    def test_a_narrower_image_loses_its_top_and_bottom(self):
        assert fit(Image.new("RGB", (1000, 1000)), 900, 450).size == (900, 450)


class TestGenerate:
    def test_the_request(self):
        gemini, transport, _ = client(image_response())
        gemini.generate(
            "m",
            "the prompt",
            [(b"a", "image/png"), (b"b", "image/png")],
            "21:9",
            "4K",
            reference=(b"r", "image/png"),
        )
        url, body, key = transport.requests[0]
        assert url.endswith("/models/m:generateContent")
        assert key == KEY and KEY not in url and KEY not in json.dumps(body)
        parts = body["contents"][0]["parts"]
        assert parts[0] == {"text": "the prompt"}
        assert [p["inlineData"]["data"] for p in parts if "inlineData" in p] == [
            base64.b64encode(x).decode() for x in (b"a", b"b", b"r")
        ]
        assert "Reference image" in parts[3]["text"]
        assert body["generationConfig"]["imageConfig"] == {
            "aspectRatio": "21:9",
            "imageSize": "4K",
        }

    def test_no_image_size_is_sent_when_none_is_asked_for(self):
        gemini, transport, _ = client(image_response())
        gemini.generate("m", "p", [(b"a", "image/png")], "21:9", None)
        assert transport.requests[0][1]["generationConfig"]["imageConfig"] == {
            "aspectRatio": "21:9"
        }

    def test_returns_the_image_and_what_the_response_said(self):
        data = png_bytes((10, 10))
        gemini, _, _ = client(image_response(data))
        raw, mime, about = gemini.generate("m", "p", [], "1:1", None)
        assert (raw, mime) == (data, "image/png")
        assert about == {
            "model_version": "gemini-3-pro-image-001",
            "text": "Here it is.",
        }

    def test_an_answer_without_an_image_is_an_error(self):
        answer = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "I cannot."}]},
                    "finishReason": "IMAGE_SAFETY",
                }
            ]
        }
        gemini, _, _ = client(answer)
        with pytest.raises(ComposeError, match="IMAGE_SAFETY.*I cannot"):
            gemini.generate("m", "p", [], "1:1", None)

    def test_a_blocked_prompt_is_an_error(self):
        gemini, _, _ = client({"promptFeedback": {"blockReason": "SAFETY"}})
        with pytest.raises(ComposeError, match="blocked: SAFETY"):
            gemini.generate("m", "p", [], "1:1", None)


class TestRetries:
    def test_a_server_error_is_retried(self):
        gemini, transport, sleeps = client(ApiError(503, "busy"), image_response())
        gemini.generate("m", "p", [], "1:1", None)
        assert len(transport.requests) == 2 and sleeps == [5]

    def test_the_suggested_delay_is_used(self):
        gemini, _, sleeps = client(ApiError(429, "slow down", 17.0), image_response())
        gemini.generate("m", "p", [], "1:1", None)
        assert sleeps == [17.0]

    def test_gives_up_after_the_retries(self):
        gemini, transport, _ = client(*[ApiError(500, "down")] * 3)
        with pytest.raises(ApiError):
            gemini.generate("m", "p", [], "1:1", None)
        assert len(transport.requests) == 3

    def test_a_client_error_is_not_retried(self):
        gemini, transport, _ = client(ApiError(400, "bad"))
        with pytest.raises(ApiError, match="bad"):
            gemini.generate("m", "p", [], "1:1", None)
        assert len(transport.requests) == 1

    def test_a_zero_quota_says_billing_and_is_not_retried(self):
        gemini, transport, _ = client(
            ApiError(429, "Quota exceeded ... limit: 0, model: m")
        )
        with pytest.raises(ApiError, match="enable billing"):
            gemini.generate("m", "p", [], "1:1", None)
        assert len(transport.requests) == 1

    def test_no_credit_says_so(self):
        gemini, _, _ = client(ApiError(402, "depleted"))
        with pytest.raises(ApiError, match="no credit"):
            gemini.generate("m", "p", [], "1:1", None)


class TestHttpPost:
    def test_the_key_is_scrubbed_from_an_error(self, monkeypatch):
        body = json.dumps(
            {"error": {"message": f"bad key {KEY}", "details": [{"retryDelay": "12s"}]}}
        )

        def refuse(request, timeout, context):
            assert request.get_header("X-goog-api-key") == KEY
            assert KEY not in request.full_url
            raise urllib.error.HTTPError(
                request.full_url, 400, "Bad", {}, io.BytesIO(body.encode())
            )

        monkeypatch.setattr(compose.urllib.request, "urlopen", refuse)
        with pytest.raises(ApiError) as caught:
            compose.http_post("https://example.invalid/x", {}, KEY)
        assert KEY not in str(caught.value) and "<key>" in str(caught.value)
        assert caught.value.retry_delay == 12.0


class TestScreening:
    def test_the_schema_asks_every_truth(self):
        truths = [Truth("numerals", "Any numerals?", False)]
        gemini, transport, _ = client(
            screen_response(
                {
                    "watch_count": 5,
                    "case_cut_off": False,
                    "answers": {"numerals": False},
                }
            )
        )
        answer = gemini.read("s", b"img", "image/png", truths)
        body = transport.requests[0][1]
        schema = body["generationConfig"]["responseSchema"]
        assert schema["properties"]["answers"]["required"] == ["numerals"]
        assert "numerals: Any numerals?" in body["contents"][0]["parts"][0]["text"]
        assert answer["watch_count"] == 5

    def test_with_no_truths_no_answers_are_asked_for(self):
        gemini, transport, _ = client(
            screen_response({"watch_count": 1, "case_cut_off": False})
        )
        gemini.read("s", b"img", "image/png", [])
        assert (
            "answers"
            not in transport.requests[0][1]["generationConfig"]["responseSchema"][
                "properties"
            ]
        )

    def test_judging_the_answer(self):
        truths = [Truth("numerals", "q", False), Truth("one-time", "q", True)]
        checks = model_checks(
            {"watch_count": 6, "case_cut_off": False, "answers": {"numerals": True}},
            5,
            truths,
        )
        assert [(c.name, c.passed) for c in checks] == [
            ("watch-count", False),
            ("inside-frame", True),
            ("numerals", False),
            ("one-time", False),
        ]
        assert checks[3].detail == "answered nothing, want yes"


def request_for(tmp_path, **overrides):
    values = dict(
        inputs=captures(tmp_path),
        prompt_template="Exactly $count_word watches.",
        output_directory=str(tmp_path / "out"),
    )
    values.update(overrides)
    return Request(**values)


GOOD = {"watch_count": 3, "case_cut_off": False}


class TestCompose:
    def test_a_hand_made_candidate_is_sized_screened_and_recorded(self, tmp_path):
        source = write_png(tmp_path / "gemini.png", (2520, 1080), (200, 200, 200))
        gemini, transport, _ = client(screen_response(GOOD))
        request = request_for(
            tmp_path, sources=[source], sizes=[(1440, 720), (900, 450)]
        )

        [result] = compose.compose(request, gemini, CLOCK)

        assert result.accepted
        out = tmp_path / "out"
        assert Image.open(out / "candidate-01.png").size == (1440, 720)
        assert Image.open(out / "candidate-01-900x450.png").size == (900, 450)
        assert (out / "candidate-01-original.png").read_bytes() == (
            tmp_path / "gemini.png"
        ).read_bytes()
        sidecar = json.loads((out / "candidate-01.json").read_text())
        assert sidecar["model"] is None
        assert sidecar["source"]["file"] == "gemini.png"
        assert sidecar["timestamp"] == "2026-10-06T12:00:00+00:00"
        assert sidecar["prompt_sha256"] == compose._sha256(b"Exactly three watches.")
        assert sidecar["accepted"] is True
        assert [len(transport.requests)] == [1]

    def test_the_key_is_never_written(self, tmp_path):
        gemini, _, _ = client(image_response(), screen_response(GOOD))
        compose.compose(
            request_for(tmp_path, generate=True, candidates=1), gemini, CLOCK
        )
        for path in (tmp_path / "out").iterdir():
            assert KEY.encode() not in path.read_bytes()

    def test_a_generated_candidate_records_its_model(self, tmp_path):
        gemini, transport, _ = client(image_response(), screen_response(GOOD))
        [result] = compose.compose(
            request_for(tmp_path, generate=True, candidates=1), gemini, CLOCK
        )
        sidecar = json.loads(open(result.sidecar).read())
        assert sidecar["model"] == "gemini-3-pro-image"
        assert sidecar["model_version"] == "gemini-3-pro-image-001"
        assert sidecar["parameters"]["aspect_ratio"] == "21:9"
        assert sidecar["parameters"]["image_size"] == "4K"
        assert transport.requests[0][0].endswith("gemini-3-pro-image:generateContent")
        assert len(transport.requests[0][1]["contents"][0]["parts"]) == 4

    def test_a_rejected_candidate_is_kept_and_marked(self, tmp_path):
        source = write_png(tmp_path / "gemini.png", (2520, 1080))
        gemini, _, _ = client(
            screen_response({"watch_count": 4, "case_cut_off": False})
        )
        [result] = compose.compose(
            request_for(tmp_path, sources=[source]), gemini, CLOCK
        )
        assert not result.accepted
        assert result.paths[0].endswith("candidate-01-rejected.png")
        sidecar = json.loads(open(result.sidecar).read())
        assert sidecar["accepted"] is False
        assert {
            "name": "watch-count",
            "passed": False,
            "detail": "4 seen, want 3",
        } in sidecar["checks"]

    def test_too_large_a_file_is_rejected(self, tmp_path):
        source = write_png(tmp_path / "gemini.png", (2520, 1080))
        [result] = compose.compose(
            request_for(tmp_path, sources=[source], screen_model=None, max_kb=0.001),
            None,
            CLOCK,
        )
        assert [(c.name, c.passed) for c in result.checks] == [
            ("size", True),
            ("file-size", False),
        ]

    def test_nothing_is_overwritten(self, tmp_path):
        source = write_png(tmp_path / "gemini.png", (2520, 1080))
        request = request_for(tmp_path, sources=[source, source], screen_model=None)
        first = compose.compose(request, None, CLOCK)
        second = compose.compose(request, None, CLOCK)
        assert [r.index for r in first + second] == [1, 2, 3, 4]

    def test_a_failed_generation_is_recorded_and_the_rest_continue(self, tmp_path):
        answer = {
            "candidates": [{"content": {"parts": []}, "finishReason": "IMAGE_SAFETY"}]
        }
        gemini, _, _ = client(answer, image_response(), screen_response(GOOD))
        results = compose.compose(
            request_for(tmp_path, generate=True, candidates=2), gemini, CLOCK
        )
        assert [r.accepted for r in results] == [False, True]
        assert results[0].sidecar.endswith("candidate-01-failed.json")
        assert "IMAGE_SAFETY" in json.loads(open(results[0].sidecar).read())["error"]

    def test_a_screening_failure_rejects_rather_than_aborts(self, tmp_path):
        source = write_png(tmp_path / "gemini.png", (2520, 1080))
        gemini, _, _ = client(ApiError(400, "nope"))
        [result] = compose.compose(
            request_for(tmp_path, sources=[source]), gemini, CLOCK
        )
        assert not result.accepted
        assert result.checks[-1].name == "screening"

    def test_needs_exactly_one_source_of_candidates(self, tmp_path):
        with pytest.raises(ComposeError, match="not both"):
            compose.compose(request_for(tmp_path), None)
        with pytest.raises(ComposeError, match="not both"):
            compose.compose(
                request_for(tmp_path, sources=["x.png"], generate=True), None
            )

    def test_screening_needs_a_client(self, tmp_path):
        with pytest.raises(ComposeError, match="client"):
            compose.compose(request_for(tmp_path, sources=["x.png"]), None)


class TestCli:
    def prompt(self, tmp_path, text="$count_word watches, light $light"):
        path = tmp_path / "prompt.txt"
        path.write_text(text)
        return str(path)

    def test_prints_the_filled_prompt(self, tmp_path, capsys):
        status = cli.main(
            [
                "compose",
                "-p",
                self.prompt(tmp_path),
                "--print-prompt",
                "--var",
                "light=left",
            ]
            + captures(tmp_path)
        )
        assert status == 0
        assert capsys.readouterr().out == "three watches, light left"

    def test_var_overrides_vars(self, tmp_path, capsys):
        values = tmp_path / "vars.json"
        values.write_text('{"light": "above", "unused": 3}')
        cli.main(
            [
                "compose",
                "-p",
                self.prompt(tmp_path),
                "--print-prompt",
                "--vars",
                str(values),
                "--var",
                "light=below",
            ]
            + captures(tmp_path)
        )
        assert capsys.readouterr().out.endswith("light below")

    def test_needs_candidates_or_generate(self, tmp_path):
        with pytest.raises(SystemExit):
            cli.main(
                ["compose", "-p", self.prompt(tmp_path, "x"), "-o", str(tmp_path / "o")]
                + captures(tmp_path)
            )

    def test_sizes_and_checks_hand_made_candidates_without_a_key(
        self, tmp_path, capsys, monkeypatch
    ):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        source = write_png(tmp_path / "gemini.png", (2520, 1080))
        out = tmp_path / "o"
        status = cli.main(
            [
                "compose",
                "-p",
                self.prompt(tmp_path, "x"),
                "-o",
                str(out),
                "--no-screen",
                "-c",
                source,
            ]
            + captures(tmp_path)
        )
        assert status == 0
        assert Image.open(out / "candidate-01.png").size == (1440, 720)
        assert "1 of 1 accepted" in capsys.readouterr().out

    def test_exits_non_zero_when_nothing_is_accepted(self, tmp_path):
        source = write_png(tmp_path / "gemini.png", (2520, 1080))
        status = cli.main(
            [
                "compose",
                "-p",
                self.prompt(tmp_path, "x"),
                "-o",
                str(tmp_path / "o"),
                "--no-screen",
                "--max-kb",
                "1",
                "-q",
                "-c",
                source,
            ]
            + captures(tmp_path)
        )
        assert status == 1

    def test_screening_without_a_key_is_an_error(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        source = write_png(tmp_path / "gemini.png", (2520, 1080))
        with pytest.raises(SystemExit):
            cli.main(
                [
                    "compose",
                    "-p",
                    self.prompt(tmp_path, "x"),
                    "-o",
                    str(tmp_path / "o"),
                    "-c",
                    source,
                ]
                + captures(tmp_path)
            )
