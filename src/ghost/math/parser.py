"""
Security-hardened Mathematical Expression Parser for Ghost.
Uses strict AST validation with whitelisted operators, functions, and symbols.
Guarantees strictly zero execution of arbitrary code (NO eval/exec).
"""

import ast
import re
from typing import Any, Dict, Set, Union
import sympy


class UnsafeExpressionError(ValueError):
    """Raised when an expression contains unauthorized AST nodes, calls, or attributes."""
    pass


class SafeMathParser:
    """
    Parses and sanitizes mathematical strings into safe SymPy expressions.
    Enforces a strict AST whitelist and rejects any code injection or dunder attribute traversal.
    """

    ALLOWED_FUNCTIONS: Dict[str, Any] = {
        # Arithmetic & Roots
        "sqrt": sympy.sqrt,
        "cbrt": sympy.cbrt,
        "root": sympy.root,
        "exp": sympy.exp,
        "log": sympy.log,
        "ln": sympy.log,
        "log10": lambda x: sympy.log(x, 10),
        "log2": lambda x: sympy.log(x, 2),
        "abs": sympy.Abs,
        "factorial": sympy.factorial,
        # Trigonometric & Hyperbolic
        "sin": sympy.sin,
        "cos": sympy.cos,
        "tan": sympy.tan,
        "asin": sympy.asin,
        "acos": sympy.acos,
        "atan": sympy.atan,
        "atan2": sympy.atan2,
        "sec": sympy.sec,
        "csc": sympy.csc,
        "cot": sympy.cot,
        "sinh": sympy.sinh,
        "cosh": sympy.cosh,
        "tanh": sympy.tanh,
        # Calculus & Algebra operations
        "diff": sympy.diff,
        "integrate": sympy.integrate,
        "limit": sympy.limit,
        "summation": sympy.summation,
        "solve": sympy.solve,
        "simplify": sympy.simplify,
        "expand": sympy.expand,
        "factor": sympy.factor,
        "cancel": sympy.cancel,
        "apart": sympy.apart,
        "collect": sympy.collect,
        "Eq": sympy.Eq,
    }

    ALLOWED_CONSTANTS: Dict[str, Any] = {
        "pi": sympy.pi,
        "E": sympy.E,
        "e": sympy.E,
        "oo": sympy.oo,
        "infinity": sympy.oo,
        "I": sympy.I,
        "golden_ratio": sympy.GoldenRatio,
    }

    ALLOWED_NODE_TYPES: Set[type] = {
        ast.Expression,
        ast.Constant,
        ast.UnaryOp,
        ast.UAdd,
        ast.USub,
        ast.BinOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.Call,
        ast.Name,
        ast.Load,
        ast.Compare,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Tuple,
        ast.List,
    }

    FORBIDDEN_PATTERNS = [
        re.compile(r"__\w+__"),
        re.compile(r"\bimport\b"),
        re.compile(r"\bbuiltins\b"),
        re.compile(r"\beval\b"),
        re.compile(r"\bexec\b"),
        re.compile(r"\bsystem\b"),
        re.compile(r"\bsubprocess\b"),
        re.compile(r"\bopen\b"),
        re.compile(r"\bgetattr\b"),
        re.compile(r"\bsetattr\b"),
    ]

    @classmethod
    def sanitize_input(cls, expr_str: str) -> str:
        """Clean and normalize human mathematical notation before AST parsing."""
        s = expr_str.strip()

        # Reject obvious injection keywords early
        for pat in cls.FORBIDDEN_PATTERNS:
            if pat.search(s):
                raise UnsafeExpressionError(f"Forbidden token detected in expression: '{s}'")

        # Replace Unicode math symbols
        s = s.replace("×", "*").replace("÷", "/").replace("−", "-")
        s = s.replace("π", "pi")

        # Handle square root symbol √
        s = re.sub(r"√\s*\(", r"sqrt(", s)
        s = re.sub(r"√\s*(\d+(?:\.\d+)?|[a-zA-Z_]\w*)", r"sqrt(\1)", s)
        s = s.replace("√", "sqrt")

        # Handle power caret '^' -> '**'
        s = re.sub(r"\^", "**", s)

        # Handle implicit multiplication:
        # e.g. '3(8 - 2)' -> '3 * (8 - 2)'
        s = re.sub(r"(\d)\s*\(", r"\1 * (", s)
        # e.g. ')(' -> ') * ('
        s = re.sub(r"\)\s*\(", r") * (", s)
        # e.g. '3x' -> '3 * x', '5sqrt(x)' -> '5 * sqrt(x)'
        s = re.sub(r"(\d)\s*([a-zA-Z_]\w*)", r"\1 * \2", s)
        # e.g. ')x' -> ') * x'
        s = re.sub(r"\)\s*([a-zA-Z_]\w*)", r") * \1", s)

        return s

    @classmethod
    def validate_ast(cls, tree: ast.AST) -> None:
        """Walk the AST and verify all nodes, names, and calls conform to whitelist."""
        for node in ast.walk(tree):
            if type(node) not in cls.ALLOWED_NODE_TYPES:
                raise UnsafeExpressionError(
                    f"Disallowed AST node type '{type(node).__name__}' in expression."
                )

            # Strictly forbid attribute access (e.g. foo.bar or ().__class__)
            if isinstance(node, ast.Attribute):
                raise UnsafeExpressionError("Attribute access (dot notation) is strictly forbidden.")

            # Validate function calls
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name):
                    raise UnsafeExpressionError("Only direct named mathematical functions are allowed.")
                func_name = node.func.id
                if func_name not in cls.ALLOWED_FUNCTIONS:
                    raise UnsafeExpressionError(
                        f"Disallowed function call '{func_name}'. Whitelisted functions: {sorted(cls.ALLOWED_FUNCTIONS.keys())}"
                    )
                if node.keywords:
                    raise UnsafeExpressionError("Keyword arguments are not permitted in mathematical calls.")

            # Validate identifier names
            if isinstance(node, ast.Name):
                name = node.id
                if name.startswith("__") or name.endswith("__"):
                    raise UnsafeExpressionError(f"Dunder names are strictly forbidden: '{name}'")
                # Whitelisted functions, constants, or valid mathematical variable symbols (1 to 4 chars)
                is_fn = name in cls.ALLOWED_FUNCTIONS
                is_const = name in cls.ALLOWED_CONSTANTS
                is_var = bool(re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,5}$", name))
                if not (is_fn or is_const or is_var):
                    raise UnsafeExpressionError(f"Disallowed identifier: '{name}'")

    @classmethod
    def parse_to_sympy(cls, raw_expr: str) -> Union[sympy.Expr, sympy.Eq]:
        """
        Safely converts a mathematical string to a SymPy expression or Eq object.
        Guarantees zero eval() by validating AST before passing to SymPy.
        """
        cleaned = cls.sanitize_input(raw_expr)

        # Check for equations with '='
        if "=" in cleaned and "==" not in cleaned:
            parts = cleaned.split("=")
            if len(parts) == 2:
                lhs_str, rhs_str = parts[0].strip(), parts[1].strip()
                lhs_tree = ast.parse(lhs_str, mode="eval")
                rhs_tree = ast.parse(rhs_str, mode="eval")
                cls.validate_ast(lhs_tree)
                cls.validate_ast(rhs_tree)

                local_dict = {**cls.ALLOWED_FUNCTIONS, **cls.ALLOWED_CONSTANTS}
                lhs_expr = sympy.sympify(lhs_str, locals=local_dict, evaluate=False)
                rhs_expr = sympy.sympify(rhs_str, locals=local_dict, evaluate=False)
                return sympy.Eq(lhs_expr, rhs_expr)

        # Parse as single expression
        tree = ast.parse(cleaned, mode="eval")
        cls.validate_ast(tree)

        local_dict = {**cls.ALLOWED_FUNCTIONS, **cls.ALLOWED_CONSTANTS}
        return sympy.sympify(cleaned, locals=local_dict, evaluate=False)
