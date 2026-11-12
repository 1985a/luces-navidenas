# Tareas habituales. Todo corre sin abrir ventanas ni sonar.
.PHONY: test lint check paquete

test:
	python -I -W error::ResourceWarning -X dev -m unittest discover -s tests

lint:
	uvx ruff check .

check: lint test

paquete: check
	makepkg -f
