import json, subprocess, os, sys, glob
D="/mnt/pool/videos"
rows=[]
for f in sorted(os.listdir(D)):
    p=os.path.join(D,f)
    if not os.path.isfile(p) or f.startswith("."): continue
    try:
        o=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration,size,bit_rate:stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate","-of","json",p],capture_output=True,text=True,timeout=60).stdout
        j=json.loads(o)
    except Exception as e:
        rows.append({"file":f,"error":str(e)}); continue
    v=[s for s in j.get("streams",[]) if s.get("codec_type")=="video"]
    a=[s for s in j.get("streams",[]) if s.get("codec_type")=="audio"]
    if not v: rows.append({"file":f,"error":"novideo","size":os.path.getsize(p)}); continue
    v=v[0]; n,d=(v.get("avg_frame_rate") or "0/1").split("/")
    fps=float(n)/float(d) if float(d) else 0
    fm=j["format"]
    rows.append(dict(file=f,size=int(fm["size"]),dur=float(fm.get("duration",0)),w=v["width"],h=v["height"],fps=round(fps,2),vcodec=v["codec_name"],audio=bool(a),kbps=int(fm.get("bit_rate",0))//1000))
json.dump(rows,open("corpus.json","w"),indent=1)
print(len(rows))
