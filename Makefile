PLUGINS := readmoo hyread pubu
DIST_DIR := dist

.PHONY: all clean $(PLUGINS)

all: $(PLUGINS)

$(PLUGINS):
	@mkdir -p $(DIST_DIR)
	@cd $@ && zip -r ../$(DIST_DIR)/$@.zip __init__.py
	@echo "  → $(DIST_DIR)/$@.zip"

clean:
	rm -rf $(DIST_DIR)
