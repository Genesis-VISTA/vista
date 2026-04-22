import sys, os
from pathlib import Path
import matplotlib.pyplot as plt

VISTA_OUT = Path(os.environ['VISTA_OUT'])

print("args:", sys.argv)

categories = ["A", "B", "C", "D"]
values = [23, 45, 12, 37]
plt.bar(categories, values)
plt.title("Example Bar Chart")
plt.savefig(VISTA_OUT / "chart.png")
print("Saved figure to chart.png")
