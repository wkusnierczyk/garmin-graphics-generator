"""
Composing a hero with an image model, from the face-on captures.

A hero that reads as a product shot needs watches seen from several viewpoints,
overlapping, under one light. A 2D transform cannot produce that (#18): a warped
capture has no pixels for the case's side wall, lugs or crown, and its face-on
lighting is baked in. An image model can, given the captures and a precise prompt.

What it returns cannot be trusted as it comes. It has returned six watches from five
inputs, and numerals in a rain that has none. So every candidate is screened before
anyone looks at it: the exact size and the file size locally, and the watch count and
the project's own screen truths by a second, vision-only model call. Rejected
candidates are kept, marked, so a near miss can still be judged by eye.

Generating through the API is paid: image generation has no free tier. Generating in
the Gemini app is covered by a subscription. So by default the candidates are images
made by hand from the prompt this command prints, and the command only sizes and
screens them; ``generate`` makes the API calls instead, for when that is worth paying
for.

This is the only command that needs a network and a key, and the only one whose
output differs from run to run. Promotion stays a human act: nothing here ever
overwrites a file, so a published hero is put in place by hand.
"""
import base64
import hashlib
import http.client
import io
import json
import logging
import os
import re
import ssl
import string
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
KEY_VARIABLE = "GEMINI_API_KEY"

# Nano Banana Pro: the strongest at keeping several distinct inputs distinct.
DEFAULT_MODEL = "gemini-3-pro-image"
# Screening only reads an image, which a text model with vision does on the free tier.
DEFAULT_SCREEN_MODEL = "gemini-3.8-flash"
DEFAULT_IMAGE_SIZE = "4K"
DEFAULT_SIZE = (1440, 720)
# Connect IQ rejects a hero over 2048 KB.
DEFAULT_MAX_KB = 2048
DEFAULT_RETRIES = 2
REQUEST_TIMEOUT = 600
MAX_RETRY_DELAY = 60

# What generateContent accepts as imageConfig.aspectRatio, as the API itself listed
# them on 2026-10-06. There is no 2:1, so a store hero is cut from 21:9.
ASPECT_RATIOS = (
    "1:1",
    "1:4",
    "1:8",
    "2:3",
    "3:2",
    "3:4",
    "4:1",
    "4:3",
    "4:5",
    "5:4",
    "8:1",
    "9:16",
    "16:9",
    "21:9",
)
IMAGE_SIZES = ("512px", "1K", "2K", "4K")

FORMATS = {"png": ("PNG", "png"), "jpg": ("JPEG", "jpg")}
MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
PILLOW_EXTENSIONS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}
# What a flattened candidate shows where it was transparent: the prompt's background.
FLATTEN_BACKGROUND = (255, 255, 255)
# A Gemini key is printable ASCII with no spaces. Anything else would fail as an HTTP
# header with the key quoted in the error, so it is refused here without echoing it.
KEY_PATTERN = re.compile(r"[\x21-\x7e]+")

NUMBER_WORDS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen".split()
)

CANDIDATE = re.compile(r"^candidate-(\d+)\b")


class ComposeError(Exception):
    """A problem with the inputs, the key or the model's answer."""

    # Whether the next call would fail the same way; see ApiError.
    fatal = False


class ApiError(ComposeError):
    """The API refused a request."""

    def __init__(
        self,
        status: int,
        message: str,
        retry_delay: Optional[float] = None,
        fatal: bool = False,
    ):
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status
        self.retry_delay = retry_delay
        # Fatal: the next call would fail the same way -- billing, the key, a bad
        # request -- so a run stops rather than repeating it for every candidate.
        self.fatal = fatal or (400 <= status < 500 and status != 429)


class Check(NamedTuple):
    """One screening test of one candidate."""

    name: str
    passed: bool
    detail: str


class Truth(NamedTuple):
    """
    A face-specific fact the screening model is asked about.

    ``question`` is put to the model as a yes/no question about the image, and the
    candidate passes when the answer equals ``expect``.
    """

    name: str
    question: str
    expect: bool


class Candidate(NamedTuple):
    """One generated candidate and what became of it."""

    index: int
    accepted: bool
    paths: Sequence[str]
    sidecar: str
    checks: Sequence[Check]
    error: Optional[str] = None


# A transport posts a JSON body to a URL with the key and returns the decoded JSON.
Transport = Callable[[str, dict, str], dict]


def read_key(key_file: Optional[str] = None, environ=None) -> str:
    """
    The API key, from ``key_file`` when one is named, else from ``GEMINI_API_KEY``.

    The key is never logged, and never written into any output.
    """
    if key_file:
        try:
            with open(os.path.expanduser(key_file), encoding="utf-8") as stream:
                key = stream.read().strip()
        except OSError as error:
            raise ComposeError(
                f"cannot read the key file {key_file}: {error.strerror}"
            ) from error
        if not key:
            raise ComposeError(f"the key file {key_file} is empty")
        return _checked_key(key, f"the key file {key_file}")
    key = (os.environ if environ is None else environ).get(KEY_VARIABLE, "").strip()
    if not key:
        raise ComposeError(f"no API key: set {KEY_VARIABLE} or pass --key-file")
    return _checked_key(key, KEY_VARIABLE)


def _checked_key(key: str, origin: str) -> str:
    if not KEY_PATTERN.fullmatch(key):
        raise ComposeError(
            f"{origin} does not hold just a key: it has spaces, line breaks or "
            "characters outside printable ASCII"
        )
    return key


def choose_aspect_ratio(width: int, height: int) -> str:
    """
    The offered aspect ratio nearest the target among those at least as wide.

    Cropping a wider image down to the target loses only its edges, which the prompt
    asks to be clear; a narrower one would have to lose the top or bottom.
    """
    target = width / height

    def value(ratio: str) -> float:
        across, down = ratio.split(":")
        return int(across) / int(down)

    wide_enough = [ratio for ratio in ASPECT_RATIOS if value(ratio) >= target - 1e-9]
    if not wide_enough:
        raise ComposeError(f"no offered aspect ratio is as wide as {width}x{height}")
    return min(wide_enough, key=value)


def render_prompt(template: str, variables: Dict[str, str]) -> str:
    """
    Fills ``$name`` / ``${name}`` placeholders in ``template``; ``$$`` is a dollar.

    A placeholder with no value is an error rather than left in place: a prompt that
    says ``${roll}`` to the model is a silently wrong prompt.
    """
    try:
        return string.Template(template).substitute(variables)
    except KeyError as error:
        raise ComposeError(
            f"the prompt uses ${{{error.args[0]}}}, which has no value; pass "
            f"--var {error.args[0]}=..."
        ) from error
    except ValueError as error:
        raise ComposeError(
            f"the prompt has a malformed placeholder: {error}"
        ) from error


def builtin_variables(count: int, width: int, height: int) -> Dict[str, str]:
    """The placeholders every prompt can use without passing them."""
    return {
        "count": str(count),
        "count_word": NUMBER_WORDS[count] if count < len(NUMBER_WORDS) else str(count),
        "width": str(width),
        "height": str(height),
    }


def read_truths(path: Optional[str]) -> List[Truth]:
    """
    Reads the project's screen truths: a JSON list of
    ``{"name": ..., "question": ..., "expect": true|false}``.
    """
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as stream:
            entries = json.load(stream)
    except (OSError, ValueError) as error:
        raise ComposeError(f"cannot read the checks file {path}: {error}") from error
    if not isinstance(entries, list):
        raise ComposeError(f"{path}: expected a JSON list of checks")
    truths = []
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not isinstance(entry.get("question"), str)
            or not isinstance(entry.get("expect", True), bool)
        ):
            raise ComposeError(
                f"{path}: each check needs a string name and question, and an "
                f"optional boolean expect; got {entry!r}"
            )
        truths.append(
            Truth(entry["name"], entry["question"], entry.get("expect", True))
        )
    names = [truth.name for truth in truths]
    if len(set(names)) != len(names):
        raise ComposeError(f"{path}: check names must be unique")
    return truths


def _ssl_context() -> ssl.SSLContext:
    """
    certifi's CA bundle when it is installed, else the system's.

    The python.org build for macOS has no CA certificates until its "Install
    Certificates" script is run, and every request then fails verification. certifi
    is usually present anyway: rembg's dependencies bring it.
    """
    try:
        import certifi  # pylint: disable=import-outside-toplevel
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def http_post(url: str, body: dict, key: str) -> dict:
    """Posts ``body`` to ``url`` as JSON, the key in a header, never in the URL."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT, context=_ssl_context()
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        message, delay = _api_error(raw)
        raise ApiError(error.code, message.replace(key, "<key>"), delay) from None
    except urllib.error.URLError as error:
        raise ComposeError(f"cannot reach the API: {error.reason}") from None
    except (OSError, http.client.HTTPException) as error:
        # A timeout or a dropped connection while waiting for the answer, which is
        # where a slow generation spends its time. Status 0: retried, not fatal.
        message = str(error) or type(error).__name__
        raise ApiError(
            0, f"connection failed: {message}".replace(key, "<key>")
        ) from None
    except ValueError as error:
        raise ApiError(0, f"the answer is not JSON: {error}") from None


def _api_error(raw: str) -> Tuple[str, Optional[float]]:
    """The message and the suggested retry delay, in seconds, of an error body."""
    try:
        error = json.loads(raw)["error"]
    except (ValueError, KeyError, TypeError):
        return raw.strip()[:500] or "no message", None
    delay = None
    for detail in error.get("details", []):
        match = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(detail.get("retryDelay", "")))
        if match:
            delay = float(match.group(1))
    return str(error.get("message", "no message")).strip(), delay


def _inline(data: bytes, mime_type: str) -> dict:
    return {
        "inlineData": {
            "mimeType": mime_type,
            "data": base64.b64encode(data).decode("ascii"),
        }
    }


def _mime_type(path: str) -> str:
    extension = os.path.splitext(path)[1].lower()
    if extension not in MIME_TYPES:
        raise ComposeError(
            f"{path}: not an image the API takes " f"({', '.join(sorted(MIME_TYPES))})"
        )
    return MIME_TYPES[extension]


class Gemini:
    """The two calls this command makes: generate an image, and read one."""

    def __init__(
        self,
        key: str,
        transport: Transport = http_post,
        retries: int = DEFAULT_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._key = key
        self._transport = transport
        self._retries = retries
        self._sleep = sleep

    def _call(self, model: str, body: dict) -> dict:
        url = f"{API_ROOT}/{model}:generateContent"
        attempt = 0
        while True:
            try:
                return self._transport(url, body, self._key)
            except ApiError as error:
                # A quota of zero is a billing setting, not a busy server; waiting
                # does not help, and the message should say what will.
                if error.status == 429 and "limit: 0" in str(error):
                    raise ApiError(
                        error.status,
                        f"{model} has no quota for this key. Image generation is not "
                        "on the free tier: enable billing on the key's Google Cloud "
                        "project.",
                        fatal=True,
                    ) from None
                if error.status == 402:
                    raise ApiError(
                        error.status,
                        "the key's project has no credit left. Top it up in AI "
                        "Studio, or screen without a model (--no-screen).",
                    ) from None
                if attempt >= self._retries or not (
                    error.status in (0, 429) or error.status >= 500
                ):
                    raise
                # The server's delay is honoured, never shortened: retrying early
                # only earns another 429. Past the cap, waiting is not worth it.
                if error.retry_delay and error.retry_delay > MAX_RETRY_DELAY:
                    raise ApiError(
                        error.status,
                        f"{error} (the API asks to wait "
                        f"{error.retry_delay:.0f} s; try again later)",
                    ) from None
                delay = error.retry_delay or 5 * 2**attempt
                logger.warning("%s: %s; retrying in %.0f s", model, error, delay)
                self._sleep(delay)
                attempt += 1

    def generate(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        self,
        model: str,
        prompt: str,
        images: Sequence[Tuple[bytes, str]],
        aspect_ratio: str,
        image_size: Optional[str],
        reference: Optional[Tuple[bytes, str]] = None,
    ) -> Tuple[bytes, str, dict]:
        """
        One image from the prompt and the inputs.

        Returns the image bytes, their MIME type, and what the response said about
        itself: the model version that served it, and any text it returned.
        """
        parts = [{"text": prompt}]
        parts += [_inline(data, mime_type) for data, mime_type in images]
        if reference:
            parts.append(
                {
                    "text": "Reference image, not a watch to place: what the glyphs "
                    "on the screens look like."
                }
            )
            parts.append(_inline(*reference))
        image_config = {"aspectRatio": aspect_ratio}
        if image_size:
            image_config["imageSize"] = image_size
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": image_config,
            },
        }
        response = self._call(model, body)
        about = {"model_version": response.get("modelVersion")}
        feedback = response.get("promptFeedback", {})
        if feedback.get("blockReason"):
            raise ComposeError(f"the prompt was blocked: {feedback['blockReason']}")
        candidates = response.get("candidates") or []
        if not candidates:
            raise ComposeError("the model returned no candidate")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        texts = [
            part["text"] for part in parts if "text" in part and not part.get("thought")
        ]
        if texts:
            about["text"] = "\n".join(texts)
        for part in parts:
            inline = part.get("inlineData")
            if inline and not part.get("thought"):
                return (
                    base64.b64decode(inline["data"]),
                    inline.get("mimeType", "image/png"),
                    about,
                )
        reason = candidates[0].get("finishReason", "unknown")
        said = f"; it said: {texts[0][:300]}" if texts else ""
        raise ComposeError(
            f"the model returned no image (finish reason {reason}){said}"
        )

    def read(
        self,
        model: str,
        image: bytes,
        mime_type: str,
        truths: Sequence[Truth],
    ) -> dict:
        """
        Asks a vision model to count the watches and answer the project's questions.

        The answer is constrained to a JSON schema, so it is parsed rather than read.
        """
        questions = "\n".join(f"- {truth.name}: {truth.question}" for truth in truths)
        prompt = (
            "You are checking a product photograph of smartwatches before it is "
            "published. Look closely; do not guess.\n"
            "- watch_count: how many separate watch cases appear, partly hidden ones "
            "included. A strap alone is not a watch.\n"
            "- case_cut_off: is any watch case or screen cut off by the edge of the "
            "image? Straps running off the edge do not count.\n"
        )
        if truths:
            prompt += (
                "Then answer each of these yes/no questions about the image, under "
                "answers, keyed by name:\n" + questions + "\n"
            )
        prompt += "Put anything you were unsure about in notes."
        schema = {
            "type": "OBJECT",
            "properties": {
                "watch_count": {"type": "INTEGER"},
                "case_cut_off": {"type": "BOOLEAN"},
                "answers": {
                    "type": "OBJECT",
                    "properties": {truth.name: {"type": "BOOLEAN"} for truth in truths},
                    "required": [truth.name for truth in truths],
                },
                "notes": {"type": "STRING"},
            },
            "required": ["watch_count", "case_cut_off", "answers"],
        }
        if not truths:
            del schema["properties"]["answers"]
            schema["required"].remove("answers")
        body = {
            "contents": [
                {"role": "user", "parts": [{"text": prompt}, _inline(image, mime_type)]}
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        response = self._call(model, body)
        try:
            parts = response["candidates"][0]["content"]["parts"]
            text = "".join(
                part.get("text", "") for part in parts if not part.get("thought")
            )
            return json.loads(text)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ComposeError(
                f"the screening model's answer is not JSON: {error}"
            ) from error


def flatten(image: Image.Image) -> Image.Image:
    """
    The image upright and opaque RGB, its colour profile kept.

    A store hero must not be transparent; a palette image would be resized
    nearest-neighbour; and a phone photo's EXIF orientation is otherwise ignored.
    """
    profile = image.info.get("icc_profile")
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, FLATTEN_BACKGROUND)
        flat.paste(rgba, mask=rgba.getchannel("A"))
        image = flat
    elif image.mode != "RGB":
        image = image.convert("RGB")
    if profile:
        image.info["icc_profile"] = profile
    return image


def crop_size(size: Tuple[int, int], width: int, height: int) -> Tuple[int, int]:
    """The largest region of the target ratio that fits in ``size``."""
    source_width, source_height = size
    if source_width * height > source_height * width:
        return round(source_height * width / height), source_height
    return source_width, round(source_width * height / width)


def fit(image: Image.Image, width: int, height: int) -> Image.Image:
    """Crops ``image`` to the target ratio about its centre, then resizes it exactly."""
    source_width, source_height = image.size
    if source_width * height > source_height * width:
        crop_width = round(source_height * width / height)
        left = (source_width - crop_width) // 2
        box = (left, 0, left + crop_width, source_height)
    else:
        crop_height = round(source_width * height / width)
        top = (source_height - crop_height) // 2
        box = (0, top, source_width, top + crop_height)
    fitted = image.crop(box).resize((width, height), Image.Resampling.LANCZOS)
    if "icc_profile" in image.info:
        fitted.info["icc_profile"] = image.info["icc_profile"]
    return fitted


def encode(image: Image.Image, image_format: str) -> bytes:
    """The image as file bytes, in ``png`` or ``jpg``."""
    pillow_format, _ = FORMATS[image_format]
    buffer = io.BytesIO()
    options = {"optimize": True}
    if image.info.get("icc_profile"):
        options["icc_profile"] = image.info["icc_profile"]
    if pillow_format == "JPEG":
        options["quality"] = 92
    image.save(buffer, pillow_format, **options)
    return buffer.getvalue()


def local_checks(
    data: bytes,
    size: Tuple[int, int],
    max_kb: Optional[int],
    cropped: Optional[Tuple[int, int]] = None,
) -> List[Check]:
    """
    The checks that need no model: the exact size as written, the file size, and
    whether the crop had at least as many pixels as the target, so the image was
    not enlarged into softness.
    """
    with Image.open(io.BytesIO(data)) as image:
        actual = image.size
    checks = [
        Check(
            "size",
            actual == tuple(size),
            f"{actual[0]}x{actual[1]}, want {size[0]}x{size[1]}",
        )
    ]
    if cropped:
        checks.append(
            Check(
                "resolution",
                cropped[0] >= size[0],
                f"cropped to {cropped[0]}x{cropped[1]}, "
                + ("downscaled" if cropped[0] >= size[0] else "enlarged")
                + f" to {size[0]}x{size[1]}",
            )
        )
    if max_kb:
        kilobytes = len(data) / 1024
        checks.append(
            Check(
                "file-size",
                kilobytes <= max_kb,
                f"{kilobytes:.0f} KB, limit {max_kb} KB",
            )
        )
    return checks


def model_checks(answer: dict, count: int, truths: Sequence[Truth]) -> List[Check]:
    """The screening model's answer, judged against the input count and the truths."""
    checks = []
    seen = answer.get("watch_count")
    counted = isinstance(seen, int) and not isinstance(seen, bool)
    checks.append(
        Check("watch-count", counted and seen == count, f"{seen} seen, want {count}")
    )
    cut = answer.get("case_cut_off")
    checks.append(
        Check(
            "inside-frame", cut is False, "a case is cut off" if cut else "all inside"
        )
    )
    answers = answer.get("answers") or {}
    for truth in truths:
        given = answers.get(truth.name)
        checks.append(
            Check(
                truth.name,
                given is truth.expect,
                f"answered {_yes_no(given)}, want {_yes_no(truth.expect)}",
            )
        )
    return checks


def _yes_no(value) -> str:
    if value is None:
        return "nothing"
    return "yes" if value else "no"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def next_index(directory: str) -> int:
    """One past the highest candidate number in ``directory``, so nothing is overwritten."""
    highest = 0
    if os.path.isdir(directory):
        for name in os.listdir(directory):
            match = CANDIDATE.match(name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def _write_new(path: str, data: bytes):
    # "x" refuses an existing file: the never-overwrite promise does not rest on the
    # numbering alone.
    try:
        with open(path, "xb") as stream:
            stream.write(data)
    except FileExistsError:
        raise ComposeError(
            f"{path} appeared while this run was writing; is another run using "
            "the same directory? Nothing was overwritten."
        ) from None


class Request(NamedTuple):
    """
    Everything one compose run asks for.

    Candidates come from ``sources``, images generated elsewhere -- by hand, in the
    Gemini app -- or, with ``generate`` set, from ``candidates`` calls to ``model``.
    """

    inputs: Sequence[str]
    prompt_template: str
    output_directory: str
    variables: Optional[Dict[str, str]] = None
    sources: Sequence[str] = ()
    generate: bool = False
    candidates: int = 1
    reference: Optional[str] = None
    sizes: Sequence[Tuple[int, int]] = (DEFAULT_SIZE,)
    max_kb: Optional[int] = DEFAULT_MAX_KB
    image_format: str = "png"
    model: str = DEFAULT_MODEL
    image_size: Optional[str] = DEFAULT_IMAGE_SIZE
    screen_model: Optional[str] = DEFAULT_SCREEN_MODEL
    truths: Sequence[Truth] = ()


def prompt_for(request: Request) -> str:
    """The prompt, its placeholders filled; what is pasted into the Gemini app by hand."""
    if not request.inputs:
        raise ComposeError("no input images")
    if not request.sizes:
        raise ComposeError("no output size")
    width, height = request.sizes[0]
    variables = builtin_variables(len(request.inputs), width, height)
    # The built-ins are what screening judges against: a prompt asking for four
    # watches while five are counted for would reject every candidate.
    clashes = sorted(set(variables) & set(request.variables or {}))
    if clashes:
        raise ComposeError(
            f"{', '.join(clashes)} cannot be set: they come from the inputs and -s"
        )
    variables.update(request.variables or {})
    return render_prompt(request.prompt_template, variables)


def _read(path: str) -> bytes:
    try:
        with open(path, "rb") as stream:
            return stream.read()
    except OSError as error:
        raise ComposeError(f"cannot read {path}: {error.strerror}") from error


def _decodable(data: bytes, name: str):
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise ComposeError(f"{name}: not a readable image: {error}") from error


def compose(  # pylint: disable=too-many-locals
    request: Request,
    client: Optional[Gemini],
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> List[Candidate]:
    """
    Sizes and screens each candidate, generating them first when asked to.

    Every candidate gets a sidecar JSON recording where it came from (the model, or
    the source file), the prompt's hash, the parameters, the screening, and the time.
    Nothing existing is overwritten. ``client`` may be None only when nothing needs
    the API: no generation, and no screening model.

    A candidate that fails -- no image, an unreadable one, a screening error -- is
    recorded and the run goes on, except when the API says the next call would fail
    the same way (billing, the key, a bad request): then generation stops, and
    screening is skipped for the rest.
    """
    if request.generate == bool(request.sources):
        raise ComposeError("give candidate images, or ask to generate them; not both")
    if request.generate and request.candidates < 1:
        raise ComposeError("the candidate count must be at least 1")
    if request.image_format not in FORMATS:
        raise ComposeError(f"unknown format {request.image_format}")
    if client is None and (request.generate or request.screen_model):
        raise ComposeError("an API client is needed to generate or to screen")
    if request.truths and not request.screen_model:
        raise ComposeError("the checks are put to the screening model, so need one")
    prompt = prompt_for(request)
    images = [(_read(path), _mime_type(path)) for path in request.inputs]
    reference = None
    if request.reference:
        reference = (_read(request.reference), _mime_type(request.reference))
    # Every hand-made candidate is read before the first is screened, so a broken
    # file stops the run before it has paid for anything.
    supplied = []
    for path in request.sources:
        raw = _read(path)
        _mime_type(path)
        _decodable(raw, path)
        supplied.append((path, raw))
    aspect_ratio = choose_aspect_ratio(*request.sizes[0])
    try:
        os.makedirs(request.output_directory, exist_ok=True)
    except OSError as error:
        raise ComposeError(
            f"cannot use {request.output_directory} as the output directory: "
            f"{error.strerror}"
        ) from error

    record = {
        "model": request.model if request.generate else None,
        "prompt_sha256": _sha256(prompt.encode("utf-8")),
        "parameters": {
            "aspect_ratio": aspect_ratio if request.generate else None,
            "image_size": request.image_size if request.generate else None,
            "sizes": [f"{w}x{h}" for w, h in request.sizes],
            "format": request.image_format,
            "max_kb": request.max_kb,
            "variables": request.variables or {},
            "inputs": [
                {"file": os.path.basename(path), "sha256": _sha256(data)}
                for path, (data, _) in zip(request.inputs, images)
            ],
            "reference": (
                {
                    "file": os.path.basename(request.reference),
                    "sha256": _sha256(reference[0]),
                }
                if reference
                else None
            ),
            "screen_model": request.screen_model,
            "checks": [truth._asdict() for truth in request.truths],
        },
    }

    def generated():
        for _ in range(request.candidates):
            try:
                raw, mime_type, about = client.generate(
                    request.model,
                    prompt,
                    images,
                    aspect_ratio,
                    request.image_size,
                    reference,
                )
            except ComposeError as error:
                yield None, None, {"error": str(error)}
                if error.fatal:
                    return
                continue
            yield raw, mime_type, about

    def handmade():
        for path, raw in supplied:
            source = {"file": os.path.basename(path), "sha256": _sha256(raw)}
            yield raw, _mime_type(path), {"source": source}

    results = []
    screening = {"stopped": None}
    index = next_index(request.output_directory)
    for raw, mime_type, about in generated() if request.generate else handmade():
        sidecar = dict(record, timestamp=clock().isoformat(timespec="seconds"))
        sidecar.update(about)
        results.append(
            _finish(request, client, index, raw, mime_type, sidecar, screening)
        )
        index += 1
    return results


def _failed(directory, stem, sidecar, raw=None, mime_type=None) -> Candidate:
    """Records a candidate that has no image to screen, keeping any bytes it had."""
    index = int(CANDIDATE.match(stem).group(1))
    sidecar["accepted"] = False
    files = []
    if raw is not None:
        original = f"{stem}-failed-original.{EXTENSIONS.get(mime_type, 'bin')}"
        _write_new(os.path.join(directory, original), raw)
        files.append(original)
        sidecar["files"] = files
    path = os.path.join(directory, f"{stem}-failed.json")
    _write_new(path, _json(sidecar))
    logger.warning("%s: %s", stem, sidecar["error"])
    return Candidate(index, False, (), path, (), sidecar["error"])


def _finish(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    request, client, index, raw, mime_type, sidecar, screening
) -> Candidate:
    """Sizes, screens and writes one candidate, and its sidecar."""
    directory = request.output_directory
    stem = f"candidate-{index:02d}"
    if raw is None:
        return _failed(directory, stem, sidecar)

    try:
        with Image.open(io.BytesIO(raw)) as original:
            original.load()
            sidecar["original_size"] = f"{original.size[0]}x{original.size[1]}"
            original_extension = PILLOW_EXTENSIONS.get(original.format)
            flat = flatten(original)
        cropped = crop_size(flat.size, *request.sizes[0])
        sidecar["cropped_size"] = f"{cropped[0]}x{cropped[1]}"
        outputs = [
            encode(fit(flat, w, h), request.image_format) for w, h in request.sizes
        ]
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        sidecar["error"] = f"cannot size the image: {error}"
        return _failed(directory, stem, sidecar, raw, mime_type)

    checks = local_checks(outputs[0], request.sizes[0], request.max_kb, cropped)
    if request.screen_model:
        if screening["stopped"]:
            checks.append(Check("screening", False, f"skipped: {screening['stopped']}"))
        else:
            try:
                answer = client.read(
                    request.screen_model,
                    outputs[0],
                    "image/png" if request.image_format == "png" else "image/jpeg",
                    request.truths,
                )
                sidecar["screening"] = answer
                checks += model_checks(answer, len(request.inputs), request.truths)
            except ComposeError as error:
                checks.append(Check("screening", False, str(error)))
                if error.fatal:
                    screening["stopped"] = str(error)
    accepted = all(check.passed for check in checks)
    sidecar["checks"] = [check._asdict() for check in checks]
    sidecar["accepted"] = accepted

    if not accepted:
        stem += "-rejected"
    _, extension = FORMATS[request.image_format]
    paths = [
        os.path.join(
            directory,
            f"{stem}{'' if position == 0 else f'-{w}x{h}'}.{extension}",
        )
        for position, (w, h) in enumerate(request.sizes)
    ]
    original_path = os.path.join(
        directory,
        f"{stem}-original." f"{original_extension or EXTENSIONS.get(mime_type, 'bin')}",
    )
    for path, data in zip(paths, outputs):
        _write_new(path, data)
    _write_new(original_path, raw)
    sidecar["files"] = [os.path.basename(path) for path in paths + [original_path]]
    sidecar_path = os.path.join(directory, f"{stem}.json")
    _write_new(sidecar_path, _json(sidecar))
    return Candidate(index, accepted, paths, sidecar_path, checks)


def _json(data: dict) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
