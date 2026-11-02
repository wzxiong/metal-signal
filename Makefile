.PHONY: all fetch backfill build test serve clean

all: fetch build

fetch:
	python -m scripts.fetch_data

backfill:
	python -m scripts.backfill

build:
	python -m scripts.build_site

test:
	python -m unittest discover -s tests -t . -v

serve: build
	python -m http.server 8080 --directory docs

clean:
	rm -rf scripts/__pycache__ tests/__pycache__ .pytest_cache
