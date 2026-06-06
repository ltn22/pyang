
check:
	# The checking with PYANG does not work
	# I did not checked but I think I may be caused by the fact
	# that the implementer used YANG statements not compiled YANG Data Model
	# (My) crappy cause then confuses grouping sid-file with sx:structure sid-file
	# as the former is, in the ietf-sid-file.yang, below the sx:structure
	pyang --strict ietf-sid-file-extension@2026-04-28.yang -p .:../ietf

check-yanglint:
	yanglint -p .:../ietf ietf-sid-file-extension@2026-04-28.yang
