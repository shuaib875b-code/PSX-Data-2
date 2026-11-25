import json, sys
u = json.load(open("state/intel_universe.json")); S = u.get("symbols", u)
done = {"LUCK","OGDC","FFC","ILP","SYS","MEBL","KOHP","ALAC"}
L = sorted(k for k, v in S.items() if v.get("company_class") == "operating" and v.get("instrument_type") == "equity" and v.get("scope") in ("in_scope", "check") and k not in done)
a, n = int(sys.argv[1]), int(sys.argv[2])
if len(sys.argv) > 3: print(len(L))
else: print(",".join(L[a:a + n]))
