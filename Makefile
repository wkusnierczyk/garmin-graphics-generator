.PHONY: install test test-all lint clean build docker-build docker-run format

install:
	# Installs the package + test deps + dev deps (pylint, tox, black, isort)
	pip install -e .[test,dev]

test:
	pytest tests/

test-all:
	tox

lint:
	pylint src/garmin_graphics_generator

format:
	isort src tests
	black src tests

clean:
	rm -rf build dist src/*.egg-info output .tox .pytest_cache .coverage

build:
	python -m build

docker-build:
	docker build -t garmin-gen .

docker-run:
	docker run --rm garmin-gen --about