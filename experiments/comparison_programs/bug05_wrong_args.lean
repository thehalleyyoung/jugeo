-- Bug 05: Wrong number of arguments to function
def greet (name : String) (greeting : String) : String :=
  s!"{greeting}, {name}!"

-- Too many arguments
#eval greet "Alice" "Hi" "Extra"

-- Too few arguments (missing required second argument)
#eval greet "Bob"
