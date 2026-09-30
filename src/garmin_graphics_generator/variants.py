"""
Which builds of a watch face to capture, when it is reviewed across its settings.

A face with settings has to be seen at more than its defaults, and the obvious way
to see a setting -- change it in the simulator's settings dialog -- is exactly the
kind of hand work ``shots`` exists to remove. This module decides what to build
instead: it reads the settings a project declares, picks the combinations worth
looking at, and writes each one into the project as the properties' *defaults*.

That last part is the mechanism, and it is proved rather than hoped for. A fresh
simulator holds no settings file for the app, so it draws every property at the
default the build declares. Rewriting that default is one build per combination,
which is slow, but the alternative -- writing the simulator's ``.SET`` file
directly -- means producing a format Garmin does not document.

Nothing here touches Docker or the simulator. It turns resource files and a
strategy into a `Plan`: the builds to make, and how their frames are laid out on a
contact sheet.

The strategies exist because the full cartesian product is rarely what anyone
wants and grows fast:

* ``sweep`` varies one setting at a time with the rest at their defaults, so the
  cost is the sum of the value counts. It answers "what does each setting do".
* ``grid`` is the product of two named settings, as rows by columns. It answers
  "how do these two interact".
* ``cases`` is an explicit list of combinations, for the handful that matter.
* ``all`` is the full product of the varied settings, refused above a cap unless
  forced, so a large run is never started by accident.
"""
import itertools
import json
import os
import re
import xml.etree.ElementTree as ElementTree
from xml.sax.saxutils import escape
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

# Above this many combinations, `all` asks to be forced. A capture is a build and a
# simulator start, which is a minute or more under emulation.
ALL_CAP = 24

SWEEP = "sweep"
GRID = "grid"
CASES = "cases"
ALL = "all"
STRATEGIES = (SWEEP, GRID, CASES, ALL)

# Setting types whose values can be listed from the resources alone. Anything else
# -- numbers, colours, free text -- has no finite set of values to try, so its
# values have to be given.
_BOOLEAN_VALUES = (("true", "true"), ("false", "false"))


class VariantsError(Exception):
    """Raised when the settings to vary cannot be read or do not make sense."""


class Setting(NamedTuple):
    """One setting that can be varied: its key, how it is titled, and its values."""

    key: str
    title: str
    default: str
    # (value as the property file holds it, label as the settings screen shows it)
    values: Tuple[Tuple[str, str], ...]

    def label_of(self, value: str) -> str:
        """The label the settings screen gives a value, or the value itself."""
        for candidate, label in self.values:
            if candidate == value:
                return label
        return value


class Property(NamedTuple):
    """
    A declared property: its default, and every file that declares it.

    A property can be declared on more than one resource path -- a base file and
    a device or edition override -- and which one a build takes is monkeyc's
    business. So every declaration is rewritten, and the default reported is the
    last one read.
    """

    key: str
    default: str
    paths: Tuple[str, ...]


class Tile(NamedTuple):
    """One frame on the contact sheet: which combination, and what to call it."""

    combination: int
    label: str


class Row(NamedTuple):
    """A row of tiles, with a heading when the row means something on its own."""

    heading: str
    tiles: Tuple[Tile, ...]


class Plan(NamedTuple):
    """
    What to build, and how to lay the frames out.

    ``combinations`` holds each distinct combination once, as the settings that
    differ from their defaults; ``{}`` is the defaults. A sweep shows the defaults
    once per setting, but builds it only once. ``columns`` is set for a grid, where
    each column is one value of the second setting.
    """

    strategy: str
    settings: Tuple[Setting, ...]
    combinations: Tuple[Dict[str, str], ...]
    rows: Tuple[Row, ...]
    columns: Optional[Tuple[str, ...]] = None


class Resources(NamedTuple):
    """The settings and properties a project declares, as read from its files."""

    properties: Dict[str, Property]
    settings: Dict[str, Setting]


def _xml_files(directory: str) -> List[str]:
    """Every XML file under a resource directory, in a stable order."""
    found = []
    for root, _directories, names in os.walk(directory):
        for name in names:
            if name.lower().endswith(".xml"):
                found.append(os.path.join(root, name))
    return sorted(found)


def _parse(path: str):
    try:
        return ElementTree.parse(path).getroot()
    except ElementTree.ParseError as error:
        raise VariantsError(f"cannot read {path}: {error}") from error


def _resolve(text: str, strings: Dict[str, str]) -> str:
    """A label as the settings screen shows it: ``@Strings.X`` looked up, else as is."""
    text = (text or "").strip()
    if text.startswith("@Strings."):
        return strings.get(text[len("@Strings.") :], text[len("@Strings.") :])
    return text


def read_resources(project: str, resource_directories: Sequence[str]) -> Resources:
    """
    Reads the properties, settings and strings under the given resource directories.

    The directories are the build's resource path, relative to the project. Which
    ones a build uses is the jungle's business, and a jungle can compute them, so
    they are named rather than guessed: a file on some other edition's path would
    be varied here and ignored by the build, and every tile would look the same.

    Elements are found wherever they are, not by file name or root element: a
    resource file may be ``<strings>`` or a ``<resources>`` holding strings,
    settings and properties together, and monkeyc takes either.
    """
    properties: Dict[str, Property] = {}
    settings_elements = []
    strings: Dict[str, str] = {}

    for directory in resource_directories:
        absolute = os.path.realpath(os.path.join(project, directory))
        root = os.path.realpath(project)
        if absolute != root and not absolute.startswith(root + os.sep):
            # An overlay is laid over the project; a file outside it has nowhere
            # to go, and would be varied without the build ever seeing it.
            raise VariantsError(f"{directory} is outside the project {project}")
        if not os.path.isdir(absolute):
            raise VariantsError(f"{project} has no resource directory {directory}")
        for path in _xml_files(absolute):
            root = _parse(path)
            for element in root.iter("property"):
                key = element.get("id")
                if key:
                    earlier = properties[key].paths if key in properties else ()
                    properties[key] = Property(
                        key,
                        (element.text or "").strip(),
                        earlier + ((path,) if path not in earlier else ()),
                    )
            settings_elements.extend(root.iter("setting"))
            for element in root.iter("string"):
                if element.get("id"):
                    strings[element.get("id")] = (element.text or "").strip()

    settings: Dict[str, Setting] = {}
    for element in settings_elements:
        key = (element.get("propertyKey") or "").replace("@Properties.", "")
        if key not in properties:
            continue
        config = element.find("settingConfig")
        kind = config.get("type") if config is not None else None
        if kind == "list":
            values = tuple(
                (entry.get("value"), _resolve(entry.text, strings))
                for entry in config.findall("listEntry")
            )
        elif kind == "boolean":
            values = _BOOLEAN_VALUES
        else:
            values = ()
        settings[key] = Setting(
            key,
            _resolve(element.get("title") or key, strings),
            properties[key].default,
            values,
        )

    return Resources(properties, settings)


def parse_assignment(text: str) -> Tuple[str, List[str]]:
    """``key=v1,v2`` as the key and its values."""
    key, separator, values = text.partition("=")
    key = key.strip()
    parsed = [value.strip() for value in values.split(",") if value.strip()]
    if not separator or not key or not parsed:
        raise VariantsError(f"expected KEY=VALUE[,VALUE...], not {text!r}")
    return key, parsed


def choose_settings(
    resources: Resources,
    vary: Sequence[str] = (),
    assignments: Sequence[str] = (),
) -> List[Setting]:
    """
    The settings to vary, in the order they were named, with their values.

    ``vary`` narrows to the named settings; with neither ``vary`` nor
    ``assignments``, every setting whose values can be listed is varied.
    ``assignments`` (``key=v1,v2``) give or replace a setting's values, and name it
    to be varied too. A property with no settings entry can be varied this way:
    it is a default like any other, and the build draws it.
    """
    explicit: Dict[str, List[str]] = {}
    for assignment in assignments:
        key, values = parse_assignment(assignment)
        explicit[key] = values

    names = []
    for key in list(vary) + list(explicit):
        if key not in names:
            names.append(key)
    if not names:
        names = [key for key, setting in resources.settings.items() if setting.values]
        if not names:
            raise VariantsError(
                "found no settings with values to vary; name them with --set"
            )

    chosen = []
    for key in names:
        if key not in resources.properties:
            known = ", ".join(sorted(resources.properties)) or "none"
            raise VariantsError(f"no property {key!r}; the properties are: {known}")
        declared = resources.settings.get(key)
        if key in explicit:
            labels = dict(declared.values) if declared else {}
            values = tuple((value, labels.get(value, value)) for value in explicit[key])
        elif declared and declared.values:
            values = declared.values
        else:
            raise VariantsError(
                f"{key!r} is not a list or a boolean setting, so its values cannot "
                f"be listed; give them with --set {key}=VALUE,VALUE"
            )
        chosen.append(
            Setting(
                key,
                declared.title if declared else key,
                resources.properties[key].default,
                values,
            )
        )
    return chosen


def _overrides(settings: Sequence[Setting], values: Sequence[str]) -> Dict[str, str]:
    """A combination as the settings that differ from their defaults."""
    return {
        setting.key: value
        for setting, value in zip(settings, values)
        if value != setting.default
    }


class _Combinations:
    """Distinct combinations in first-seen order, each built once however often shown."""

    def __init__(self):
        self.items: List[Dict[str, str]] = []

    def index(self, combination: Dict[str, str]) -> int:
        if combination not in self.items:
            self.items.append(combination)
        return self.items.index(combination)


def _mark_default(setting: Setting, value: str) -> str:
    label = setting.label_of(value)
    return f"{label} (default)" if value == setting.default else label


def plan_sweep(settings: Sequence[Setting]) -> Plan:
    """One row per setting, every value of it, the others at their defaults."""
    combinations = _Combinations()
    rows = []
    for setting in settings:
        tiles = tuple(
            Tile(
                combinations.index(_overrides([setting], [value])),
                _mark_default(setting, value),
            )
            for value, _label in setting.values
        )
        rows.append(Row(f"{setting.title} ({setting.key})", tiles))
    return Plan(SWEEP, tuple(settings), tuple(combinations.items), tuple(rows))


def plan_grid(settings: Sequence[Setting], across: str, down: str) -> Plan:
    """``down`` as rows, ``across`` as columns, every pair of their values."""
    by_key = {setting.key: setting for setting in settings}
    for key in (down, across):
        if key not in by_key:
            raise VariantsError(f"the grid names {key!r}, which is not being varied")
    if down == across:
        raise VariantsError("a grid needs two different settings")
    rows_setting, columns_setting = by_key[down], by_key[across]

    combinations = _Combinations()
    rows = []
    for row_value, _ in rows_setting.values:
        tiles = tuple(
            Tile(
                combinations.index(
                    _overrides(
                        [rows_setting, columns_setting], [row_value, column_value]
                    )
                ),
                "",
            )
            for column_value, _ in columns_setting.values
        )
        rows.append(
            Row(
                f"{rows_setting.title}: {_mark_default(rows_setting, row_value)}", tiles
            )
        )
    columns = tuple(
        f"{columns_setting.title}: {_mark_default(columns_setting, value)}"
        for value, _ in columns_setting.values
    )
    return Plan(
        GRID,
        (rows_setting, columns_setting),
        tuple(combinations.items),
        tuple(rows),
        columns,
    )


def _describe(settings: Sequence[Setting], combination: Dict[str, str]) -> str:
    """A tile label naming every setting that is not at its default."""
    by_key = {setting.key: setting for setting in settings}
    parts = [
        f"{by_key[key].title}: {by_key[key].label_of(value)}"
        if key in by_key
        else f"{key}: {value}"
        for key, value in combination.items()
    ]
    return "\n".join(parts) if parts else "defaults"


def plan_all(
    settings: Sequence[Setting], cap: int = ALL_CAP, force: bool = False
) -> Plan:
    """The full product of the varied settings, one tile each."""
    total = 1
    for setting in settings:
        total *= len(setting.values)
    if total > cap and not force:
        raise VariantsError(
            f"all {total} combinations is more than {cap}; narrow them with --vary, "
            "or pass --force"
        )
    combinations = _Combinations()
    tiles = []
    for values in itertools.product(*[[v for v, _ in s.values] for s in settings]):
        combination = _overrides(settings, values)
        tiles.append(
            Tile(combinations.index(combination), _describe(settings, combination))
        )
    return Plan(
        ALL, tuple(settings), tuple(combinations.items), (Row("", tuple(tiles)),)
    )


def _as_property_text(value, key: str) -> str:
    """A JSON value as a properties file holds it: ``true``, not ``True``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int)):
        return str(value)
    raise VariantsError(
        f"{key} is {json.dumps(value)} in a case; give a string, integer or boolean"
    )


def plan_cases(resources: Resources, cases_path: str) -> Plan:
    """
    The combinations listed in a JSON file, one tile each.

    The file is a list of objects mapping a property key to its value, e.g.
    ``[{"timeSize": "6", "timeStyle": "1"}, {}]``; ``{}`` is the defaults.
    Values are taken as the property file would hold them.
    """
    try:
        with open(cases_path, "r", encoding="utf-8") as cases_file:
            cases = json.load(cases_file)
    except (OSError, ValueError) as error:
        raise VariantsError(
            f"cannot read the cases in {cases_path}: {error}"
        ) from error
    if not isinstance(cases, list) or not all(isinstance(c, dict) for c in cases):
        raise VariantsError(f"{cases_path} must hold a list of objects")
    if not cases:
        raise VariantsError(f"{cases_path} lists no cases")

    keys: List[str] = []
    for case in cases:
        for key in case:
            if key not in resources.properties:
                raise VariantsError(f"a case in {cases_path} names no property {key!r}")
            if key not in keys:
                keys.append(key)
    settings = [
        resources.settings.get(key)
        or Setting(key, key, resources.properties[key].default, ())
        for key in keys
    ]

    combinations = _Combinations()
    tiles = []
    for case in cases:
        values = [
            _as_property_text(case.get(s.key, s.default), s.key) for s in settings
        ]
        combination = _overrides(settings, values)
        tiles.append(
            Tile(combinations.index(combination), _describe(settings, combination))
        )
    return Plan(
        CASES, tuple(settings), tuple(combinations.items), (Row("", tuple(tiles)),)
    )


def rewrite_defaults(text: str, values: Dict[str, str]) -> Tuple[str, List[str]]:
    """
    A properties file with the named properties' defaults replaced.

    Edited as text rather than through an XML library, which would drop the file's
    comments and reflow it; the build only needs the values changed, and a copy
    that differs from the original in nothing else is easy to check by eye.
    Comments are matched and left alone, so a property commented out is neither
    changed nor counted as found. Values are escaped for XML. Returns the new
    text and the keys it found.
    """
    found: List[str] = []

    def replace(match):
        if match.group("key") is None:
            return match.group(0)
        found.append(match.group("key"))
        value = escape(values[match.group("key")])
        return match.group("open") + value + match.group("close")

    if not values:
        return text, found
    keys = "|".join(re.escape(key) for key in values)
    pattern = re.compile(
        r"<!--.*?-->"
        r"|(?P<open><property\b[^>]*?\bid\s*=\s*(?P<quote>[\"'])(?P<key>"
        + keys
        + r")(?P=quote)[^>]*>)"
        r"(?P<value>[^<]*)(?P<close></property>)",
        re.DOTALL,
    )
    return pattern.sub(replace, text), found


def overlay_files(
    project: str,
    resources: Resources,
    combinations: Sequence[Dict[str, str]],
) -> List[Dict[str, str]]:
    """
    Each combination's properties files, as their paths relative to the project
    mapped to their text.

    Every combination carries every properties file that any combination
    touches, changed or not, so the overlays can be laid over one copy of the
    project in sequence: each puts back what the last one changed.
    """
    project = os.path.realpath(project)
    touched = sorted(
        {
            path
            for combination in combinations
            for key in combination
            for path in resources.properties[key].paths
        }
    )
    originals = {}
    for path in touched:
        with open(path, "r", encoding="utf-8") as source:
            originals[path] = source.read()

    overlays = []
    for combination in combinations:
        files = {}
        for path in touched:
            wanted = {
                key: value
                for key, value in combination.items()
                if path in resources.properties[key].paths
            }
            text, found = rewrite_defaults(originals[path], wanted)
            missing = set(wanted) - set(found)
            if missing:
                raise VariantsError(
                    f"could not rewrite {', '.join(sorted(missing))} in {path}"
                )
            files[os.path.relpath(path, project)] = text
        overlays.append(files)
    return overlays


def slug(combination: Dict[str, str]) -> str:
    """A combination as a file name: ``timeSize-6_timeStyle-1``, or ``defaults``."""
    if not combination:
        return "defaults"
    text = "_".join(f"{key}-{value}" for key, value in combination.items())
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text)
