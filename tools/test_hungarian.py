"""Verify the Hungarian solver against brute force on random matrices."""
import itertools, random, sys
sys.path.insert(0, "tools")
import numpy as np
from visual_d3 import hungarian

random.seed(7)
bad = 0
for trial in range(200):
    n = random.randint(2, 6)
    m = np.array([[random.randint(0, 9) for _ in range(n)] for _ in range(n)], dtype=float)
    got = hungarian(m)
    if sorted(got) != list(range(n)):
        print("not a permutation:", got); bad += 1; continue
    got_cost = sum(m[i, got[i]] for i in range(n))
    best = min(sum(m[i, p[i]] for i in range(n)) for p in itertools.permutations(range(n)))
    if abs(got_cost - best) > 1e-9:
        print(f"MISMATCH n={n}: got {got_cost}, best {best}\n{m}"); bad += 1
        if bad > 2: break

# rectangular padding case as used by align(): 2 android, 3 hmos
n, m2 = 2, 3
size = n + m2
big = np.full((size, size), 1e6)
big[:n, :m2] = [[0.1, 0.9, 0.9], [0.9, 0.2, 0.9]]
for i in range(n):
    big[i, m2 + i] = 0.42
for j in range(m2):
    big[n + j, j] = 0.42
big[n:, m2:] = 0.0
ass = hungarian(big)
print("padded assignment:", ass, "-> pairs",
      [(i, ass[i]) for i in range(n) if ass[i] < m2],
      "unmatched android", [i for i in range(n) if ass[i] >= m2])
print("brute-force validation:", "OK" if bad == 0 else f"{bad} failures")
