# Makefile for Judgment Geometry paper series
# Usage: make all    — compile all papers
#        make paper01 — compile just paper 1
#        make clean   — remove build artifacts

PAPERS = paper00-seminal \
         paper01-semantic-sites \
         paper02-judgment-algebra \
         paper03-descent-obstructions \
         paper04-trust-algebra \
         paper05-smt-dispatch \
         paper06-semantic-moves \
         paper07-python-effects \
         paper08-treaty-synthesis \
         paper09-proof-carrying-python \
         paper10-evaluation

PDFS = $(addsuffix .pdf, $(PAPERS))

.PHONY: all clean

all: $(PDFS)

%.pdf: %.tex jugeo-common.tex
	pdflatex -interaction=nonstopmode $< || true
	pdflatex -interaction=nonstopmode $<

clean:
	rm -f *.aux *.log *.out *.toc *.bbl *.blg *.nav *.snm *.vrb *.fls *.fdb_latexmk $(PDFS)

# Convenience targets
paper00: paper00-seminal.pdf
paper01: paper01-semantic-sites.pdf
paper02: paper02-judgment-algebra.pdf
paper03: paper03-descent-obstructions.pdf
paper04: paper04-trust-algebra.pdf
paper05: paper05-smt-dispatch.pdf
paper06: paper06-semantic-moves.pdf
paper07: paper07-python-effects.pdf
paper08: paper08-treaty-synthesis.pdf
paper09: paper09-proof-carrying-python.pdf
paper10: paper10-evaluation.pdf
