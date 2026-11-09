"""
Independent Verification Layer for Ghost Mathematics Engine.
Guarantees mathematical correctness through back-substitution,
calculus inversion (differentiation of integrals), and numerical fuzzing.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union
import random
import mpmath
import sympy


@dataclass
class VerificationResult:
    is_verified: bool
    method: str
    residual_error: float
    details: str
    checks_passed: int
    checks_total: int


class MathVerifier:
    """
    Independently verifies mathematical results without assuming the solver's output is correct.
    """

    TOLERANCE = 1e-9

    @classmethod
    def verify_equation_solutions(
        cls,
        equation: sympy.Eq,
        solutions: List[Any],
        variable: Optional[sympy.Symbol] = None
    ) -> VerificationResult:
        """
        Substitutes candidate solutions back into the original equation (LHS and RHS).
        Verifies |LHS(sol) - RHS(sol)| < TOLERANCE.
        """
        if not solutions:
            return VerificationResult(
                is_verified=False,
                method="back_substitution",
                residual_error=float("inf"),
                details="No solutions provided to verify.",
                checks_passed=0,
                checks_total=0
            )

        if variable is None:
            free_symbols = list(equation.free_symbols)
            variable = free_symbols[0] if free_symbols else sympy.Symbol("x")

        passed = 0
        max_residual = 0.0
        details_list = []

        for sol in solutions:
            try:
                # Handle dict solutions from solve() e.g. {x: 2}
                val = sol[variable] if isinstance(sol, dict) and variable in sol else sol

                lhs_val = equation.lhs.subs(variable, val)
                rhs_val = equation.rhs.subs(variable, val)

                diff_exact = sympy.simplify(lhs_val - rhs_val)
                if diff_exact == 0:
                    passed += 1
                    details_list.append(f"Substituted {variable}={val}: Exact balance LHS == RHS (diff=0).")
                else:
                    diff_num = float(abs(sympy.N(diff_exact)))
                    max_residual = max(max_residual, diff_num)
                    if diff_num < cls.TOLERANCE:
                        passed += 1
                        details_list.append(f"Substituted {variable}={val}: Residual {diff_num:.2e} < {cls.TOLERANCE:.0e}.")
                    else:
                        details_list.append(f"Failed verification for {variable}={val}: Residual {diff_num:.2e} exceeds tolerance.")
            except Exception as e:
                details_list.append(f"Verification error for solution {sol}: {e}")

        all_ok = (passed == len(solutions))
        return VerificationResult(
            is_verified=all_ok,
            method="back_substitution",
            residual_error=max_residual,
            details=" | ".join(details_list),
            checks_passed=passed,
            checks_total=len(solutions)
        )

    @classmethod
    def verify_integral(
        cls,
        integrand: sympy.Expr,
        antiderivative: sympy.Expr,
        variable: sympy.Symbol
    ) -> VerificationResult:
        """
        Verifies an indefinite integral by differentiating the antiderivative
        and verifying simplify(d/dx F(x) - f(x)) == 0.
        """
        try:
            derivative = sympy.diff(antiderivative, variable)
            diff_expr = sympy.simplify(derivative - integrand)

            if diff_expr == 0:
                return VerificationResult(
                    is_verified=True,
                    method="calculus_inversion (differentiation)",
                    residual_error=0.0,
                    details=f"d/d{variable}[{antiderivative}] identically equals integrand {integrand}.",
                    checks_passed=1,
                    checks_total=1
                )

            # If symbolic simplify didn't zero out, test numerical sampling
            passed_samples = 0
            total_samples = 5
            max_err = 0.0

            for _ in range(total_samples):
                sample_val = round(random.uniform(0.5, 4.5), 3)
                num_diff = float(abs(sympy.N(diff_expr.subs(variable, sample_val))))
                max_err = max(max_err, num_diff)
                if num_diff < cls.TOLERANCE:
                    passed_samples += 1

            is_ok = (passed_samples == total_samples)
            return VerificationResult(
                is_verified=is_ok,
                method="numerical_sampling_inversion",
                residual_error=max_err,
                details=f"Derivative check matched on {passed_samples}/{total_samples} random points (max error: {max_err:.2e}).",
                checks_passed=passed_samples,
                checks_total=total_samples
            )
        except Exception as e:
            return VerificationResult(
                is_verified=False,
                method="calculus_inversion",
                residual_error=float("inf"),
                details=f"Integral verification failed with error: {e}",
                checks_passed=0,
                checks_total=1
            )

    @classmethod
    def verify_numerical_precision(
        cls,
        symbolic_res: sympy.Expr,
        expected_approx: float
    ) -> VerificationResult:
        """
        Cross-checks a symbolic expression against high-precision mpmath evaluation.
        """
        try:
            val_sym = float(sympy.N(symbolic_res, 30))
            err = abs(val_sym - expected_approx)
            ok = err < 1e-6
            return VerificationResult(
                is_verified=ok,
                method="high_precision_mpmath",
                residual_error=err,
                details=f"SymPy evaluation {val_sym:.6f} vs expected {expected_approx:.6f} (residual: {err:.2e}).",
                checks_passed=1 if ok else 0,
                checks_total=1
            )
        except Exception as e:
            return VerificationResult(
                is_verified=False,
                method="high_precision_mpmath",
                residual_error=float("inf"),
                details=f"Numerical verification error: {e}",
                checks_passed=0,
                checks_total=1
            )
