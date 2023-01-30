.PHONY: dev-compile-dependencies dev-sync-dependencies

dev-compile-dependencies:
	pip-compile dev/requirements.in
	pip-compile dev/requirements-dev.in


dev-sync-dependencies:
	pip-sync dev/requirements-dev.txt

dev-test:
	pytest

dev-test-coverage-html:
	coverage run -m pytest && coverage html
	xdg-open htmlcov/index.html
