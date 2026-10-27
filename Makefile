IMAGE := lcd-stage1
DOCKER := docker run --rm $(if $(shell test -t 0 && echo y),-it) -v "$(CURDIR)":/work -w /work $(IMAGE)

.PHONY: image shell tokens ast run build phi tests check clean

image:
	docker build -t $(IMAGE) .

shell:
	$(DOCKER) bash

tokens:
	$(DOCKER) python3 lexer.py lexer_demo.txt

ast:
	$(DOCKER) python3 compiler.py --ast input.txt

run:
	$(DOCKER) bash -c 'python3 compiler.py input.txt output.ll && lli output.ll'

build:
	$(DOCKER) bash -c '\
		python3 compiler.py input.txt output.ll && \
		llc -filetype=obj -relocation-model=pic output.ll -o output.o && \
		clang -fPIE output.o -o program && \
		./program'

phi:
	$(DOCKER) bash -c '\
		python3 compiler.py input.txt output.ll && \
		opt -passes=mem2reg -S output.ll'

tests:
	$(DOCKER) ./run_tests.sh

check:
	$(DOCKER) python3 check.py

clean:
	rm -f output.ll output.o output.check.ll program
