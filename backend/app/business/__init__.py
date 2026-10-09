"""Business mode (v3 phase 8d): the system runs a small resale company, not a list of deals.

Pure functions over plain numbers (the database side is in ``service.py``). Where a number cannot come
from the user's own history it is an *assumption*, flagged as such in every output and listed in
docs/LIMITATIONS.md: nothing here pretends to have measured what it has not.
"""
