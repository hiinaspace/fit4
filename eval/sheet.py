import json,subprocess,os,sys
from PIL import Image,ImageDraw
D="/mnt/pool/videos"
r=[x for x in json.load(open("corpus.json")) if "error" not in x and x["vcodec"] not in("png","mjpeg") and x["dur"]>25 and "sync-conflict" not in x["file"] and not x["file"].startswith("SOS") and x["dur"]<7000]
# dedupe: skip clips that are cuts ([..-..]) of others
r=[x for x in r if "[" not in x["file"]]
out=[]
for i,x in enumerate(r):
    f=f"/tmp/claude-1000/sc/fr/{i}.jpg"
    t=x["dur"]*0.5
    if not os.path.exists(f):
        subprocess.run(["ffmpeg","-v","error","-y","-ss",str(t),"-i",os.path.join(D,x["file"]),"-frames:v","1","-vf","scale=320:-2",f])
    out.append((i,x,f))
json.dump([(i,x["file"]) for i,x,f in out],open("/tmp/claude-1000/sc/idx.json","w"))
N=len(out);per=20
for s in range(0,N,per):
    ch=out[s:s+per]
    W,H=320,180
    im=Image.new("RGB",(5*W,4*(H+14)),"black");d=ImageDraw.Draw(im)
    for k,(i,x,f) in enumerate(ch):
        try:
            fr=Image.open(f);fr.thumbnail((W,H));im.paste(fr,((k%5)*W,(k//5)*(H+14)))
        except Exception as e: pass
        d.text(((k%5)*W+2,(k//5)*(H+14)+H+1),f"{i} {x['file'][:40]}",fill="white")
    im.save(f"/tmp/claude-1000/sc/sheet{s//per}.jpg",quality=80)
print(N)
