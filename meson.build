# Copyright (c) 2025, Postgres Professional

# Does not support the PGXS infrastructure at this time. Please, compile as part
# of the contrib source tree.

pg_query_state_sources = files(
  'pg_query_state.c',
  'signal_handler.c',
)

if host_system == 'windows'
  pg_query_state_sources += rc_lib_gen.process(win32ver_rc, extra_args: [
    '--NAME', 'pg_query_state',
    '--FILEDESC', 'pg_query_state - provides facility to know the current state of query execution on working backend.',])
endif

pg_query_state = shared_module('pg_query_state',
  pg_query_state_sources,
  kwargs: contrib_mod_args,
)
contrib_targets += pg_query_state

extversion = '1.1'
output_name = 'pg_query_state--' + extversion + '.sql'

configure_file(
  input: 'init.sql',
  output: output_name,
  copy: true,
  install: true,
  install_dir: contrib_data_args['install_dir'],
)

install_data(
  'pg_query_state.control',
  'pg_query_state--1.0--1.1.sql',
  kwargs: contrib_data_args,
)

tests += {
  'name': 'pg_query_state',
  'sd': meson.current_source_dir(),
  'bd': meson.current_build_dir(),
  'isolation': {
    'specs': [
      'corner_cases',
    ],
    'regress_args': [
      '--temp-config', files('test.conf'),
      '--load-extension=pg_query_state',
    ],
  },
}
