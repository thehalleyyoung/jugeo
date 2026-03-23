import os
import time


while True:
	# os.system("copilot -p 'Keep repairing/testing/changing whatever needs to be changed in order for jugeo to get a 100% F1 score on bug checking, equivalence checking, and spec adherence checking on the examples in test_examples.  Note that you must still follow the full mathematical semantics defined in preliminaries/theory2.tex.  You also might need to recreate the test suites in test_examples - it should be 50 program pairs which are equivalent and 50 which are not (longish), 50 programs which satisfy specs in the form of boolean-returning functions and 50 which dont, and 100 programs which either have one or more of a number of common python bugs, or none.   You also might need to add new files for implementation - but first of all, create the benchmark suites.' --allow-all-tools --autopilot --model 'gpt-5.4'")
	os.system("copilot -p 'Run all the tests in tests/ and repair the src until none fail.' --autopilot --model 'gpt-5.4' --allow-all-tools")