-- Bug 03: Type mismatch — applying arithmetic to wrong types
def safeDivide (a : String) (b : String) : Float :=
  a / b   -- error: String does not have HDiv instance for Float

#eval safeDivide "10" "0"
