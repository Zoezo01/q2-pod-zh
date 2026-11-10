# Pre-script: the analyser that guesses which functions never return guessed mp_msg, sin and tan, and every caller
# lost the code after its call. Known ones (exit, abort) still count.
# @category q2
setAnalysisOption(currentProgram, 'Non-Returning Functions - Discovered', 'false')
