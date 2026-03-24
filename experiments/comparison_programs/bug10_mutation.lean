-- Bug 10: Type error — applying list operation to wrong type
def removeNegatives (nums : String) : List Int :=
  nums.filter (· < 0)   -- error: String.filter takes Char → Bool, not Int → Bool
                        -- and returns String, not List Int

#eval removeNegatives "hello"
