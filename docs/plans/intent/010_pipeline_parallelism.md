Pipeline parallelism for 3 tickets, starting in the order A, B, C
- Each task depends on the previous one
- Assumption: a dependent task can begin planning when the preceding task is finished planning,
  and can begin building when the preceding task is finished building.
  Planning the dependent task can happen while the preceding task is being built.
- Assumption: each actor (including the human) can only do one thing at a time.
- Assumption: the build step takes a long time

Prefix shows state: i1:intent, p1:planning, tH:todo, b2:implementing, c3:checking
-- means timeline idle, && means continued working on the previous

```
A                   B                   C
i1 - new            --                  --
i1 - push           --                  --
i1 @ ask            --                  --
iH ( answer )       --                  --
i1 @ critique       --                  --
iH ( revise )       --                  --
i1 @ plan >         --                  --
pH ( adjust )       --                  --
p1 @ critique       --                  --
pH ( revise )       --                  --
pH - todo >         --                  --
tH - implement >    --                  --
b2 @ ask            i1 @ ask            --
bH ( answer )       i1 --               --
b2 @ build >        iH ( answer )       --
b2 &&               i1 @ critique       --
b2 &&               iH ( revise )       --
b2 --               pH - todo >         --
b2 --               tH - implement >    --
cH ( review )       b2 @ ask            i1 @ ask
c3 @ pr             bH ( answer )       i1 --
cH ( adjust )       b2 --               i1 --
c3 @ fix            b2 --               iH ( answer )
cH - validate       b2 --               i1 @ critique
cH - accept >       b2 @ build >        i1 &&
--                  b2 &&               iH ( revise )
--                  b2 &&               pH - todo >
--                  b2 &&               tH - implement >
--                  b2 --               b2 @ ask
--                  b2 --               bH ( answer )       
--                  b2 --               b2 @ build >        
--                  cH ( review )       b2 &&               
--                  c3 @ pr             b2 &&               
--                  cH ( adjust )       b2 &&               
--                  c3 @ fix            b2 --               
--                  cH - validate       b2 --
--                  cH - accept >       b2 --               
--                  --                  cH ( review )       
--                  --                  c3 @ pr             
--                  --                  cH ( adjust )       
--                  --                  c3 @ fix            
--                  --                  cH - validate
--                  --                  cH - accept >       
```

- The human is unsurprisingly the limiting factor: agents will often be idle
- The long agent action is fairly well hidden while the human is busy on another timeline
- Depedent tasks doesn't actually matter much: independent tasks would finish only 3 steps sooner.
    - Build of ticket B has to wait 3 steps for ticket A to be accepted
- In practice, 2 parallel tickets may be feasible, but 3 would require some quick context switches
    - For 2 parallel tickets, a simple rule of thumb (work on the ticket until @build, then switch) works ok.
