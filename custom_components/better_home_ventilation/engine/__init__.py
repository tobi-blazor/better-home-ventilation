"""Pure control engine of Better Home Ventilation.

Nothing in this package imports ``homeassistant``, does I/O, reads the clock or
logs; ``now`` is always passed in. Only relative imports are used inside the
package, so it can be imported as ``engine`` (engine tests without Home
Assistant) and as ``custom_components.better_home_ventilation.engine``.
See docs/spec/05-architecture.md#engine-and-glue.
"""
