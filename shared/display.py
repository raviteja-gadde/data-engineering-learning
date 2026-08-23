"""Rich display helpers for lab output."""

from rich.console import Console
from rich.table import Table
from rich.syntax import Syntax
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text

console = Console()


def print_table(title: str, columns: list[str], rows: list[tuple | list]):
    """Display a formatted table.

    Args:
        title: Table heading.
        columns: Column names.
        rows: Iterable of row tuples/lists (values are stringified).
    """
    table = Table(title=title, show_lines=True)
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*(str(v) for v in row))
    console.print(table)


def print_sql(sql: str):
    """Print syntax-highlighted SQL."""
    syntax = Syntax(sql.strip(), "sql", theme="monokai", line_numbers=False)
    console.print(syntax)


def print_panel(title: str, content: str):
    """Print a bordered panel with a title."""
    console.print(Panel(content, title=title, expand=False))


def print_comparison(title: str, before: str, after: str):
    """Print a side-by-side comparison of before/after text."""
    left = Panel(before, title="Before", expand=True)
    right = Panel(after, title="After", expand=True)
    console.print(Panel(Columns([left, right], equal=True), title=title, expand=True))
