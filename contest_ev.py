import csv, glob, os, re, random, statistics as st
D=r"G:\My Drive\DK\Post Contest"
FIELD=1189; FEE=0.10; ENTRIES=20
BANDS=[(1,10.0),(2,6.0),(3,4.0),(4,3.0),(5,2.0),(6,1.50),(8,1.0),(12,0.75),
       (22,0.50),(52,0.40),(117,0.30),(277,0.20)]
tot=0; prev=0
for last,pz in BANDS: tot+=(last-prev)*pz; prev=last
print(f"prize pool ${tot:.2f}   pays {BANDS[-1][0]} of {FIELD} = top "
      f"{100*BANDS[-1][0]/FIELD:.1f}%   entries ${FIELD*FEE:.2f}  "
      f"rake {100*(1-tot/(FIELD*FEE)):.1f}%")
def prize(rank):
    for last,pz in BANDS:
        if rank<=last: return pz
    return 0.0
# A) our historical percentiles, mapped onto this field
pcts=[]
for f in glob.glob(os.path.join(D,"post_entries_*.csv")):
    for r in csv.DictReader(open(f, encoding="utf-8-sig", errors="replace")):
        try: pcts.append(float(r['Pctile']))
        except (ValueError,KeyError,TypeError): pass
evA=st.mean(prize(max(1,round(p/100*FIELD))) for p in pcts)
print(f"\nA) all {len(pcts)} historical entries, percentiles mapped to this field")
print(f"   EV/entry ${evA:.4f}   20 entries cost ${ENTRIES*FEE:.2f}  "
      f"expected ${ENTRIES*evA:.2f}   ROI {100*(evA-FEE)/FEE:+.0f}%")
# B) restrict to our entries in contests of similar size (800-2000)
sizes={}
for f in glob.glob(os.path.join(D,"contest-standings-*.csv")):
    cid=re.search(r"contest-standings-(\d+)",os.path.basename(f)).group(1)
    n=sum(1 for r in csv.DictReader(open(f,encoding="utf-8-sig",errors="replace"))
          if r.get('Rank') and r.get('Points') not in (None,''))
    sizes[cid]=n
sub=[]
for f in glob.glob(os.path.join(D,"post_entries_*.csv")):
    cid=re.search(r"post_entries_(\d+)",os.path.basename(f)).group(1)
    if not (800<=sizes.get(cid,0)<=2000): continue
    for r in csv.DictReader(open(f, encoding="utf-8-sig", errors="replace")):
        try: sub.append(float(r['Pctile']))
        except (ValueError,KeyError,TypeError): pass
if sub:
    evB=st.mean(prize(max(1,round(p/100*FIELD))) for p in sub)
    print(f"\nB) only our {len(sub)} entries in 800-2000 entry contests (like this one)")
    print(f"   EV/entry ${evB:.4f}   20 entries cost ${ENTRIES*FEE:.2f}  "
          f"expected ${ENTRIES*evB:.2f}   ROI {100*(evB-FEE)/FEE:+.0f}%")
    for lbl,hi in [("1st (0.08%)",1),("top 5",5),("top 22",22),("top 117",117),("top 277 (cash)",277)]:
        k=sum(1 for p in sub if max(1,round(p/100*FIELD))<=hi)
        print(f"     {lbl:16s} {k:4d} of {len(sub)}  {100*k/len(sub):5.2f}%")
