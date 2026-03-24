-- Bug 09: Type mismatch — returning wrong type
def absoluteValue (x : Int) : String :=
  if x < 0 then -x   -- error: Int is not String
  else x              -- error: Int is not String

#eval absoluteValue (-5)
