-- Bug 04: Using an undefined variable
def processFlag (flag : Bool) : Nat :=
  if flag then
    let result := 42
    result
  else
    result   -- error: 'result' is not in scope here

#eval processFlag false
