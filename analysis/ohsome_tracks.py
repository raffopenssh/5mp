import json,urllib.request,urllib.parse,sys
BB={"chinko_mine":"24.35,8.05,24.50,8.20","chinko_park":"23.0,5.0,25.5,7.8","boma":"33.5,5.5,35.5,7.5"}
for name,bb in BB.items():
    for filt in ["highway=track","highway=* and highway!=track"]:
        data=urllib.parse.urlencode({"bboxes":bb,"filter":filt,
          "time":"2017-01-01/2026-01-01/P1Y"}).encode()
        try:
            d=json.load(urllib.request.urlopen(urllib.request.Request(
              "https://api.ohsome.org/v1/elements/length",data=data),timeout=900))
            print(name,filt,[(x["timestamp"][:4],round(x["value"]/1000,1)) for x in d["result"]],flush=True)
        except Exception as e: print(name,filt,"err",str(e)[:100],flush=True)
