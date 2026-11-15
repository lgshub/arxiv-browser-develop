import re
from dataclasses import dataclass


FIELDS = {
    "all": "all",
    "title": "ti",
    "ti": "ti",
    "abstract": "abs",
    "abs": "abs",
    "author": "au",
    "authors": "au",
    "au": "au",
    "category": "cat",
    "cat": "cat",
}
TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|[():]|[^\s():"]+')


@dataclass(frozen=True)
class Token:
    value: str
    position: int


class QueryError(ValueError):
    pass


class Parser:
    def __init__(self, text, default_field="all"):
        self.text = text
        self.default_field = FIELDS[default_field]
        self.tokens = []
        cursor = 0
        for match in TOKEN.finditer(text):
            if text[cursor : match.start()].strip():
                raise QueryError(f"Unclosed quote at character {cursor + 1}.")
            self.tokens.append(Token(match.group(), match.start()))
            cursor = match.end()
        if text[cursor:].strip():
            raise QueryError(f"Unclosed quote at character {cursor + 1}.")
        depth = 0
        for token in self.tokens:
            depth += (token.value == "(") - (token.value == ")")
            if depth > 30:
                raise QueryError("Use no more than 30 levels of parentheses.")
        if len(self.tokens) > 250:
            raise QueryError("This expression has too many terms. Use no more than 250 tokens.")
        self.index = 0

    def peek(self):
        return self.tokens[self.index].value.lower() if self.index < len(self.tokens) else None

    def fail(self, message):
        position = (
            self.tokens[self.index].position if self.index < len(self.tokens) else len(self.text)
        )
        raise QueryError(f"{message} At character {position + 1}.")

    def take(self):
        if self.peek() is None:
            self.fail("Expected a search term.")
        token = self.tokens[self.index].value
        self.index += 1
        return token

    def parse(self):
        if not self.tokens:
            self.fail("Enter a search term or expression.")
        node = self.parse_or()
        if self.peek() is not None:
            self.fail("Unexpected token.")
        return node

    def parse_or(self):
        node = self.parse_and()
        while self.peek() == "or":
            self.take()
            node = ("or", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_unary()
        while self.peek() not in (None, ")", "or"):
            operator = self.peek()
            if operator in ("and", "andnot"):
                self.take()
            right = self.parse_unary()
            if operator == "andnot":
                right = ("not", right)
            node = ("and", node, right)
        return node

    def parse_unary(self):
        if self.peek() == "not":
            self.take()
            return ("not", self.parse_unary())
        if self.peek() == "(":
            self.take()
            node = self.parse_or()
            if self.peek() != ")":
                self.fail("Expected a closing parenthesis.")
            self.take()
            return node
        if self.peek() in (None, ")", ":", "and", "or", "andnot", "in"):
            self.fail("Expected a term, quoted phrase, or opening parenthesis.")
        term = self.take()
        field = self.default_field
        negative = False
        if self.peek() == ":":
            if term.lower() not in FIELDS:
                self.fail(f"Unknown field '{term}'.")
            field = FIELDS[term.lower()]
            self.take()
            if self.peek() in (None, "(", ")", ":", "and", "or", "not", "andnot", "in"):
                self.fail("Expected a term after the field prefix.")
            term = self.take()
        else:
            if (
                self.peek() == "not"
                and self.index + 1 < len(self.tokens)
                and self.tokens[self.index + 1].value.lower() == "in"
            ):
                self.take()
                negative = True
            if self.peek() == "in":
                self.take()
                name = self.peek()
                if name not in FIELDS:
                    self.fail("Expected all, title, abstract, author, or category after 'in'.")
                field = FIELDS[self.take().lower()]
        if term.startswith('"'):
            term = term[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        if not term.strip():
            self.fail("Search terms cannot be empty.")
        if '"' in term or "\\" in term:
            self.fail("Quotes and backslashes inside terms are not supported.")
        value = f'{field}:"{term}"' if any(char.isspace() for char in term) else f"{field}:{term}"
        node = ("atom", value)
        return ("not", node) if negative else node


def _contains_not(node):
    return node[0] == "not" or (
        node[0] != "atom" and any(_contains_not(child) for child in node[1:])
    )


def _render(node):
    if node[0] == "atom":
        return node[1]
    return f"({_render(node[1])} {node[0].upper()} {_render(node[2])})"


def _clauses(node, negative=False):
    if node[0] == "atom":
        return [[(node[1], negative)]]
    if node[0] == "not":
        return _clauses(node[1], not negative)
    left = _clauses(node[1], negative)
    right = _clauses(node[2], negative)
    union = (node[0] == "or") != negative
    size = len(left) + len(right) if union else len(left) * len(right)
    if size > 128:
        raise QueryError(
            "This expression expands into too many branches. Simplify its NOT/OR groups."
        )
    return left + right if union else [a + b for a in left for b in right]


def compile_query(keywords="", field="all", expression="", anchor=None):
    if field not in {"all", "title", "abstract", "author"}:
        raise QueryError("Unknown search field.")
    if len(keywords) + len(expression) > 4000:
        raise QueryError("Keep the query under 4,000 characters.")
    keywords, expression = keywords.strip(), expression.strip()
    node = Parser(keywords, field).parse() if keywords else None
    connector = "and"
    prefix = re.match(r"^(andnot|and|or)\b\s*", expression, flags=re.IGNORECASE)
    if prefix:
        connector = prefix.group(1).lower()
        expression = expression[prefix.end() :]
        if node is None and connector != "and":
            raise QueryError("A leading OR or ANDNOT needs a main search term.")
    if expression:
        advanced = Parser(expression).parse()
        if connector == "andnot":
            advanced = ("not", advanced)
        node = (connector if connector == "or" else "and", node, advanced) if node else advanced
    elif prefix:
        raise QueryError("Expected an expression after the operator.")
    if anchor:
        node = ("and", node, ("atom", anchor)) if node else ("atom", anchor)
    if node is None:
        raise QueryError("Enter a search term or expression.")
    if not _contains_not(node):
        return _render(node)

    # arXiv has ANDNOT but no standalone NOT; expand negations into anchored branches.
    branches = []
    for clause in _clauses(node):
        positive = list(dict.fromkeys(term for term, neg in clause if not neg))
        negative = list(dict.fromkeys(term for term, neg in clause if neg))
        if set(positive) & set(negative):
            continue
        if not positive:
            raise QueryError(
                "Each OR branch needs a positive term or category before NOT. Try 'cat:cs.RO AND NOT survey'."
            )
        branch = " AND ".join(positive)
        if negative:
            branch = f"({branch}) ANDNOT ({' OR '.join(negative)})"
        branches.append(f"({branch})")
    if not branches:
        raise QueryError("These conditions contradict each other; no paper can match.")
    return " OR ".join(dict.fromkeys(branches))
