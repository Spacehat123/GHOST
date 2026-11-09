"""
Unit Tests for Ghost Mathematics Engine.
Tests safe AST parsing, security boundaries, symbolic solving, exact vs approx,
step-by-step reasoning, and independent verification.
"""

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ghost.math.parser import SafeMathParser, UnsafeExpressionError
from ghost.math.engine import MathEngine, MathResult
from ghost.math.verifier import MathVerifier


class TestGhostMathematicsEngine(unittest.TestCase):

    def test_arithmetic_radicals(self):
        """Test basic radicals and arithmetic: √144 and √(25 + 144)."""
        res1 = MathEngine.solve("√144", operation="evaluate")
        self.assertEqual(res1.exact_result, "12")
        self.assertTrue(res1.verification["verified"])

        res2 = MathEngine.solve("√(25 + 144)", operation="evaluate")
        self.assertEqual(res2.exact_result, "13")
        self.assertTrue(res2.verification["verified"])

    def test_precedence_and_fractions(self):
        """Test operator precedence 3(8 - 2)² / 6 and nested fractions."""
        res = MathEngine.solve("3(8 - 2)^2 / 6", operation="evaluate")
        self.assertEqual(res.exact_result, "18")
        self.assertTrue(res.verification["verified"])

        res_frac = MathEngine.solve("1 / (1 + 1/2)", operation="evaluate")
        self.assertEqual(res_frac.exact_result, "2/3")
        self.assertAlmostEqual(float(res_frac.approx_result), 2/3, places=7)

    def test_algebraic_equation_solving_and_back_substitution(self):
        """Test solving polynomial equations and independent back-substitution."""
        res = MathEngine.solve("x^2 - 5*x + 6 = 0", operation="solve", variable_str="x")
        self.assertEqual(res.exact_result, "[2, 3]")
        self.assertTrue(res.verification["verified"])
        self.assertEqual(res.verification["passed_checks"], "2/2")

    def test_calculus_derivatives_and_integrals(self):
        """Test differentiation and integration with independent verification."""
        diff_res = MathEngine.solve("sin(x)*exp(x)", operation="diff", variable_str="x")
        self.assertIn("cos(x)", diff_res.exact_result)
        self.assertIn("sin(x)", diff_res.exact_result)
        self.assertTrue(diff_res.verification["verified"])

        int_res = MathEngine.solve("x^2", operation="integrate", variable_str="x")
        self.assertEqual(int_res.exact_result, "x**3/3")
        self.assertTrue(int_res.verification["verified"])
        self.assertEqual(int_res.verification["method"], "calculus_inversion (differentiation)")

    def test_plot_coordinate_sampler(self):
        """Test 2D function curve sampling."""
        plot_res = MathEngine.solve("sin(x)", operation="plot", variable_str="x", plot_range=(-3.14, 3.14), plot_points=50)
        self.assertIsNotNone(plot_res.plot_data)
        self.assertGreater(plot_res.plot_data["num_points"], 40)
        self.assertTrue(plot_res.verification["verified"])

    def test_security_ast_whitelist_rejects_injections(self):
        """Test that malicious expressions and dangerous code are strictly rejected."""
        malicious = [
            '__import__("os").system("ls")',
            'open("/etc/passwd")',
            '(lambda x: x)(42)',
            '().__class__.__bases__[0]',
            'eval("2 + 2")',
            'exec("x = 1")',
            'getattr(math, "sin")',
            'import sys',
        ]
        for payload in malicious:
            with self.assertRaises((UnsafeExpressionError, SyntaxError)):
                SafeMathParser.parse_to_sympy(payload)


if __name__ == "__main__":
    unittest.main()
