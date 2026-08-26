"""Business-object services: the act and review half of the loop.

This package reads analytics output and records what people decided about it. It is
not a pipeline stage and never writes a ``fact_*`` row — a business decision must not
be able to change a measurement.

Deliberately re-exports nothing: the modules here import each other (every one of
them writes an audit row), and a convenience import at package level would turn that
into a circular import at the worst possible moment.
"""
