"""The restricted YAML front matter that tickets, reports, ADRs and agent definitions open with.

parse(text) returns the fields of the leading `---` block as a dict, or None when the text has none; split(text)
returns the fields and the body after them. A value is a string, a bracketed list or a block list: a bare `key:`
followed by `- item` lines. A bracketed list that is a JSON list of strings reads as JSON (`["a, b"]` is one item);
any other splits on commas and drops the quotes. Blank and `#` lines are skipped, and a later key replaces an earlier
one. unquoted(value) drops the quotes or backticks around a string.
"""
import json
import re


def parse(text):
    return split(text)[0]


def split(text):
    """The fields and the body after the front matter; (None, text) when there is none."""
    match = re.match(r"^---\n(.*?)\n---(?:\n|$)", text, re.S)
    if not match:
        return None, text
    fields = {}
    key = None
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.fullmatch(r"\s*-\s+(.+)", line)
        if item and key is not None and (fields[key] == "" or isinstance(fields[key], list)):
            fields[key] = (fields[key] or []) + [unquoted(item.group(1))]
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            fields[key] = bracketed(value)
        else:
            fields[key] = value
    return fields, text[match.end():]


def bracketed(value):
    try:
        items = json.loads(value)
    except ValueError:
        items = None
    if isinstance(items, list) and all(isinstance(item, str) for item in items):
        return items
    return [unquoted(item) for item in value[1:-1].split(",") if item.strip()]


def unquoted(value):
    return value.strip().strip("`\"'")
