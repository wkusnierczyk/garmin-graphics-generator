.PHONY: install test test-all lint clean build docker-build docker-run

install:
	# Installs the package + test deps + dev deps (pylint, tox)
	pip install -e .[test,dev]

test:
	pytest tests/

test-all:
	tox

lint:
	# Points to the actual package, not the src root
	pylint src/garmin_graphics_generator

clean:
	rm -rf build dist src/*.egg-info output .tox .pytest_cache .coverage

build:
	python -m build

docker-build:
	docker build -t garmin-gen .

docker-run:
	# Runs the container with the --about flag to verify it works
	docker run --rm garmin-gen --about