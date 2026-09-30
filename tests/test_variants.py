import json

import pytest

from garmin_graphics_generator import variants
from garmin_graphics_generator.variants import VariantsError

PROPERTIES = """<properties>
    <!-- size is an index; a comment the rewrite must keep -->
    <property id="size" type="number">1</property>
    <property id="style" type="number">0</property>
    <property id="glow" type="boolean">false</property>
    <property id="hue" type="number">120</property>
    <property id="hidden" type="number">7</property>
</properties>
"""

SETTINGS = """<settings>
    <setting propertyKey="@Properties.size" title="@Strings.SizeTitle">
        <settingConfig type="list">
            <listEntry value="0">@Strings.Small</listEntry>
            <listEntry value="1">@Strings.Medium</listEntry>
            <listEntry value="2">@Strings.Large</listEntry>
        </settingConfig>
    </setting>
    <setting propertyKey="@Properties.style" title="@Strings.StyleTitle">
        <settingConfig type="list">
            <listEntry value="0">@Strings.Filled</listEntry>
            <listEntry value="1">@Strings.Hollow</listEntry>
        </settingConfig>
    </setting>
    <setting propertyKey="@Properties.glow" title="Glow">
        <settingConfig type="boolean" />
    </setting>
    <setting propertyKey="@Properties.hue" title="Hue">
        <settingConfig type="numeric" />
    </setting>
</settings>
"""

# A <resources> root holding strings, as monkeyc accepts and many projects write.
STRINGS = """<resources>
    <string id="SizeTitle">Size</string>
    <string id="Small">Small</string>
    <string id="Medium">Medium</string>
    <string id="Large">Large</string>
    <string id="StyleTitle">Style</string>
    <string id="Filled">Filled</string>
    <string id="Hollow">Hollow</string>
</resources>
"""


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / "resources" / "properties").mkdir(parents=True)
    (root / "resources" / "settings").mkdir()
    (root / "extra" / "strings").mkdir(parents=True)
    (root / "resources" / "properties" / "properties.xml").write_text(PROPERTIES)
    (root / "resources" / "settings" / "settings.xml").write_text(SETTINGS)
    (root / "extra" / "strings" / "strings.xml").write_text(STRINGS)
    return root


@pytest.fixture
def resources(project):
    return variants.read_resources(str(project), ["resources", "extra"])


class TestReadResources:
    def test_list_settings_carry_their_values_and_labels(self, resources):
        size = resources.settings["size"]
        assert size.title == "Size"
        assert size.default == "1"
        assert size.values == (("0", "Small"), ("1", "Medium"), ("2", "Large"))

    def test_a_boolean_is_true_and_false(self, resources):
        assert [v for v, _ in resources.settings["glow"].values] == ["true", "false"]

    def test_a_numeric_setting_has_no_values_to_list(self, resources):
        assert resources.settings["hue"].values == ()

    def test_a_property_without_a_setting_is_still_known(self, resources):
        assert resources.properties["hidden"].default == "7"
        assert "hidden" not in resources.settings

    def test_a_missing_directory_is_named(self, project):
        with pytest.raises(VariantsError, match="no resource directory premium"):
            variants.read_resources(str(project), ["resources", "premium"])

    def test_strings_on_another_path_are_not_seen(self, project):
        """The resource path is named, not guessed; a label then falls back to its id."""
        resources = variants.read_resources(str(project), ["resources"])
        assert resources.settings["size"].title == "SizeTitle"


class TestChooseSettings:
    def test_every_listable_setting_by_default(self, resources):
        keys = [s.key for s in variants.choose_settings(resources)]
        assert keys == ["size", "style", "glow"]

    def test_vary_narrows_and_orders(self, resources):
        keys = [s.key for s in variants.choose_settings(resources, ["style", "size"])]
        assert keys == ["style", "size"]

    def test_set_gives_values_to_a_numeric_setting(self, resources):
        (hue,) = variants.choose_settings(resources, assignments=["hue=0,240"])
        assert hue.values == (("0", "0"), ("240", "240"))

    def test_set_keeps_the_labels_of_listed_values(self, resources):
        (size,) = variants.choose_settings(resources, assignments=["size=2"])
        assert size.values == (("2", "Large"),)

    def test_set_reaches_a_property_with_no_setting(self, resources):
        (hidden,) = variants.choose_settings(resources, assignments=["hidden=1,2"])
        assert hidden.default == "7"

    def test_a_numeric_setting_needs_its_values(self, resources):
        with pytest.raises(VariantsError, match="--set hue="):
            variants.choose_settings(resources, ["hue"])

    def test_an_unknown_key_lists_the_known_ones(self, resources):
        with pytest.raises(VariantsError, match="no property 'sise'.*size"):
            variants.choose_settings(resources, ["sise"])

    @pytest.mark.parametrize("text", ["size", "size=", "=1", "size=,"])
    def test_a_malformed_assignment_is_rejected(self, resources, text):
        with pytest.raises(VariantsError, match="KEY=VALUE"):
            variants.choose_settings(resources, assignments=[text])


class TestPlans:
    def test_a_sweep_builds_the_defaults_once(self, resources):
        settings = variants.choose_settings(resources, ["size", "style"])
        plan = variants.plan_sweep(settings)

        # 3 sizes + 2 styles, the defaults shared: 4 builds, not 5.
        assert plan.combinations == (
            {"size": "0"},
            {},
            {"size": "2"},
            {"style": "1"},
        )
        assert [row.heading for row in plan.rows] == ["Size (size)", "Style (style)"]
        assert [t.label for t in plan.rows[0].tiles] == [
            "Small",
            "Medium (default)",
            "Large",
        ]
        assert plan.rows[0].tiles[1].combination == plan.rows[1].tiles[0].combination

    def test_a_grid_is_rows_by_columns(self, resources):
        settings = variants.choose_settings(resources, ["size", "style"])
        plan = variants.plan_grid(settings, across="style", down="size")

        assert len(plan.combinations) == 6
        assert plan.columns == ("Style: Filled (default)", "Style: Hollow")
        assert [row.heading for row in plan.rows] == [
            "Size: Small",
            "Size: Medium (default)",
            "Size: Large",
        ]
        assert plan.combinations[plan.rows[2].tiles[1].combination] == {
            "size": "2",
            "style": "1",
        }

    def test_a_grid_needs_two_varied_settings(self, resources):
        settings = variants.choose_settings(resources, ["size", "style"])
        with pytest.raises(VariantsError, match="not being varied"):
            variants.plan_grid(settings, across="glow", down="size")
        with pytest.raises(VariantsError, match="two different"):
            variants.plan_grid(settings, across="size", down="size")

    def test_all_is_the_product(self, resources):
        settings = variants.choose_settings(resources, ["size", "style", "glow"])
        plan = variants.plan_all(settings)
        assert len(plan.combinations) == 12
        assert plan.rows[0].tiles[0].label  # every tile says what it shows

    def test_all_is_capped_unless_forced(self, resources):
        settings = variants.choose_settings(resources, ["size", "style", "glow"])
        with pytest.raises(VariantsError, match="12 combinations is more than 10"):
            variants.plan_all(settings, cap=10)
        assert len(variants.plan_all(settings, cap=10, force=True).combinations) == 12

    def test_cases_come_from_a_file(self, resources, tmp_path):
        cases = tmp_path / "cases.json"
        cases.write_text(json.dumps([{"size": "2", "glow": True}, {}, {"size": 1}]))
        plan = variants.plan_cases(resources, str(cases))

        # {"size": 1} is the default, so it is the same build as {}.
        assert plan.combinations == ({"size": "2", "glow": "true"}, {})
        labels = [t.label for t in plan.rows[0].tiles]
        assert labels == ["Size: Large\nGlow: true", "defaults", "defaults"]

    @pytest.mark.parametrize(
        "content, message",
        [
            ("{}", "list of objects"),
            ("[]", "lists no cases"),
            ('[{"nope": 1}]', "no property 'nope'"),
            ("not json", "cannot read"),
        ],
    )
    def test_bad_cases_are_reported(self, resources, tmp_path, content, message):
        cases = tmp_path / "cases.json"
        cases.write_text(content)
        with pytest.raises(VariantsError, match=message):
            variants.plan_cases(resources, str(cases))


class TestOverlays:
    def test_only_the_value_changes(self, project, resources):
        (overlay,) = variants.overlay_files(str(project), resources, [{"size": "2"}])
        text = overlay["resources/properties/properties.xml"]
        assert text == PROPERTIES.replace(
            '<property id="size" type="number">1<',
            '<property id="size" type="number">2<',
        )

    def test_every_overlay_carries_every_touched_file(self, project, resources):
        """So they can be laid over one copy in sequence: the defaults put size back."""
        overlays = variants.overlay_files(str(project), resources, [{"size": "2"}, {}])
        assert overlays[1]["resources/properties/properties.xml"] == PROPERTIES

    def test_nothing_to_change_is_no_files(self, project, resources):
        assert variants.overlay_files(str(project), resources, [{}]) == [{}]

    def test_a_key_is_not_matched_by_prefix(self):
        text = '<property id="sizeLarge">5</property><property id="size">1</property>'
        rewritten, found = variants.rewrite_defaults(text, {"size": "3"})
        assert rewritten == (
            '<property id="sizeLarge">5</property><property id="size">3</property>'
        )
        assert found == ["size"]


def test_slug_names_a_combination():
    assert variants.slug({}) == "defaults"
    assert variants.slug({"size": "2", "hue": "a b"}) == "size-2_hue-a-b"


class TestReviewFixes:
    def test_every_declaration_of_a_property_is_rewritten(self, project):
        """monkeyc decides which one a build takes; both must carry the value."""
        (project / "extra" / "properties.xml").write_text(
            '<resources><property id="size" type="number">1</property></resources>'
        )
        resources = variants.read_resources(str(project), ["resources", "extra"])
        assert len(resources.properties["size"].paths) == 2

        (overlay,) = variants.overlay_files(str(project), resources, [{"size": "2"}])
        assert sorted(overlay) == [
            "extra/properties.xml",
            "resources/properties/properties.xml",
        ]
        assert all('"size" type="number">2<' in text for text in overlay.values())

    def test_a_commented_out_property_is_left_alone(self):
        text = '<!-- <property id="size">9</property> -->\n<property id="size">1</property>'
        rewritten, found = variants.rewrite_defaults(text, {"size": "2"})
        assert rewritten == (
            '<!-- <property id="size">9</property> -->\n<property id="size">2</property>'
        )
        assert found == ["size"]

    def test_only_a_commented_out_property_is_not_found(self):
        _, found = variants.rewrite_defaults(
            '<!-- <property id="size">9</property> -->', {"size": "2"}
        )
        assert found == []

    def test_a_value_is_escaped(self):
        rewritten, _ = variants.rewrite_defaults(
            '<property id="label">x</property>', {"label": "a&b<c"}
        )
        assert rewritten == '<property id="label">a&amp;b&lt;c</property>'

    def test_a_resource_directory_outside_the_project_is_refused(self, project):
        with pytest.raises(VariantsError, match="outside the project"):
            variants.read_resources(str(project), ["../elsewhere"])

    def test_a_case_value_must_be_a_scalar(self, resources, tmp_path):
        cases = tmp_path / "cases.json"
        cases.write_text(json.dumps([{"size": None}]))
        with pytest.raises(VariantsError, match="size is null"):
            variants.plan_cases(resources, str(cases))

    def test_a_setting_named_twice_is_varied_once(self, resources):
        keys = [
            s.key
            for s in variants.choose_settings(resources, ["size", "size"], ["size=0,2"])
        ]
        assert keys == ["size"]


def test_a_single_quoted_id_is_rewritten_in_its_own_style():
    text = "<property id='size' type='number'>1</property>"
    rewritten, found = variants.rewrite_defaults(text, {"size": "2"})
    assert rewritten == "<property id='size' type='number'>2</property>"
    assert found == ["size"]


def test_mismatched_quotes_are_not_a_match():
    _, found = variants.rewrite_defaults(
        "<property id='size\">1</property>", {"size": "2"}
    )
    assert found == []
