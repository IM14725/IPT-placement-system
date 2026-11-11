"""Argon2id tuned to the OWASP minimum (19 MiB, 2 passes, 1 lane).

Django's default (100 MiB, 8 lanes) took about 1 second per check on the dev machine, and PBKDF2's
1.5M iterations took 3+ seconds. This setting is still memory-hard and verifies in well under 0.3s.
Hashes made with other parameters keep verifying and are re-hashed to these on the owner's next login.
"""

from django.contrib.auth.hashers import Argon2PasswordHasher


class IPTArgon2PasswordHasher(Argon2PasswordHasher):
    time_cost = 2
    memory_cost = 19456
    parallelism = 1
