-- Bug 08: Using Option value without unwrapping
def getUserName (userId : Nat) : Option String :=
  if userId == 1 then some "Alice" else none

-- Type error: Option String is not String
def printName (userId : Nat) : String :=
  let name := getUserName userId
  name.toUpper   -- error: Option String has no toUpper method directly

#eval printName 99
