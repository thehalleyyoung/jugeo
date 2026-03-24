-- Bug 07: Type mismatch when accessing list element
def head? (lst : List Int) : Int :=
  lst.get! 0   -- this is actually safe at type level in Lean
               -- but the index type must be Fin or Nat

-- Type error: passing wrong type for index
def badGet (lst : List Int) : Int :=
  lst.get "zero"   -- error: String is not a valid index type

#eval badGet [1, 2, 3]
