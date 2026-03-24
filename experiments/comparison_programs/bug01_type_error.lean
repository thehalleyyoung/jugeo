-- Bug 01: Type error — passing String where Int expected
def sumValues (nums : List Int) : Int :=
  nums.foldl (· + ·) 0

#eval sumValues ["a", "b", "c"]  -- error: String ≠ Int
