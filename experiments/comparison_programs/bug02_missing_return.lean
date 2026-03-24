-- Bug 02: Non-exhaustive pattern / missing return branch
def classify (x : Int) : String :=
  if x > 0 then "positive"
  else if x < 0 then "negative"
  -- missing else branch — Lean requires totality, this causes a type error
  -- (Lean's if-then-else requires both branches when used as an expression)

#eval classify 0
