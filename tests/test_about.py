import re
from pathlib import Path

from garmin_graphics_generator.cli import main

REPOSITORY_URL = "https://github.com/wkusnierczyk/garmin-graphics-generator"
PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_about_names_the_repository(capsys):
    assert main(["--about"]) == 0
    source = [
        line for line in capsys.readouterr().out.splitlines() if "source:" in line
    ]
    assert source == [f"├─ source:    {REPOSITORY_URL}"]


def test_project_urls_name_the_repository():
    urls = re.findall(r'^"(\w+)" = "(.*)"$', PYPROJECT.read_text(), re.M)
    assert dict(urls) == {"Homepage": REPOSITORY_URL, "Source": REPOSITORY_URL}
