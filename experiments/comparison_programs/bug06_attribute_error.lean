-- Bug 06: Accessing field that doesn't exist on a structure
structure Point where
  x : Float
  y : Float

def p : Point := { x := 1.0, y := 2.0 }

-- Field 'z' does not exist on Point
#eval p.z

-- Method 'magnitude' does not exist
#eval p.magnitude
