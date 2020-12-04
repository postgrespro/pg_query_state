# contrib/pg_query_state/Makefile

MODULE_big = pg_query_state
OBJS = pg_query_state.o signal_handler.o $(WIN32RES)
EXTENSION = pg_query_state
EXTVERSION = 1.1
DATA = pg_query_state--1.0--1.1.sql
DATA_built = $(EXTENSION)--$(EXTVERSION).sql
PGFILEDESC = "pg_query_state - facility to track progress of plan execution"

ISOLATIONCHECKS=corner_cases

EXTRA_CLEAN = ./isolation_output $(EXTENSION)--$(EXTVERSION).sql \
	Dockerfile ./tests/*.pyc expected/$(ISOLATIONCHECKS).out expected/$(ISOLATIONCHECKS)_2.out

ifdef USE_PGXS
PG_CONFIG ?= pg_config
PGXS := $(shell $(PG_CONFIG) --pgxs)
include $(PGXS)
else
subdir = contrib/pg_query_state
top_builddir = ../..
include $(top_builddir)/src/Makefile.global
include $(top_srcdir)/contrib/contrib-global.mk
endif

$(EXTENSION)--$(EXTVERSION).sql: init.sql
	cat $^ > $@

check: versioncheck isolationcheck

VERSION = $(MAJORVERSION)
ifneq (,$(findstring $(MAJORVERSION), 9.5 9.6 10 11 12))
	VERSION = old
else
	VERSION = new
endif

versioncheck:
	if [ -f expected/$(ISOLATIONCHECKS).out ] && [ -f expected/$(ISOLATIONCHECKS)_2.out ] ; \
	then \
		cp expected/$(ISOLATIONCHECKS).out.$(VERSION) cat expected/$(ISOLATIONCHECKS).out ; \
		cp expected/$(ISOLATIONCHECKS)_2.out.$(VERSION) cat expected/$(ISOLATIONCHECKS)_2.out ; \
	else \
		cp expected/$(ISOLATIONCHECKS).out.$(VERSION) expected/$(ISOLATIONCHECKS).out ; \
		cp expected/$(ISOLATIONCHECKS)_2.out.$(VERSION) expected/$(ISOLATIONCHECKS)_2.out ; \
	fi

submake-isolation:
	$(MAKE) -C $(top_builddir)/src/test/isolation all

isolationcheck: | submake-isolation temp-install
	$(MKDIR_P) isolation_output
	$(pg_isolation_regress_check) \
	  --temp-config $(top_srcdir)/contrib/pg_query_state/test.conf \
      --outputdir=isolation_output \
	$(ISOLATIONCHECKS)

isolationcheck-install-force: all | submake-isolation temp-install
	$(MKDIR_P) isolation_output
	$(pg_isolation_regress_installcheck) \
      --outputdir=isolation_output \
	$(ISOLATIONCHECKS)

.PHONY: versioncheck isolationcheck isolationcheck-install-force check

temp-install: EXTRA_INSTALL=contrib/pg_query_state
