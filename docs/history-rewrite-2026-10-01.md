# History rewrite — 2026-10-01

Before this branch was first pushed past `6f143d7`, a pre-push leak audit found
three lines in `docs/superpowers/handoffs/2026-09-09-agy-batch-3-pilot-h3.md` that
were a verbatim excerpt of a page in the private vault. The vault must never reach
the public `origin`, so the excerpt was removed from every commit that carried it,
and the author's personal name was replaced with the GitHub handle in the 13 legacy
proposals added in the same commit. Nothing else changed: same tree for every other
file, same authors, dates and messages.

Rewriting changed the IDs of the 81 commits from `ae613d0` onward. Older notes,
commit messages and the gitignored ledger cite the old IDs; this table resolves
them. The old commits were never published to any remote.

| old | new |
|---|---|
| `ae613d0c4b25` | `0a1d203bc01e` |
| `d0d1426a48de` | `60b860588192` |
| `d043f13ff249` | `78aad4e00ad2` |
| `57fd812f0f78` | `8487dfc9efc8` |
| `016ed43fba0d` | `a3dcd16a207a` |
| `4895739e07ef` | `59d781cf2f73` |
| `8d0beb74d2d2` | `7fcccc0288d8` |
| `7f5cf98bc443` | `3a19a6f80ce8` |
| `2441706c2422` | `7c502237afee` |
| `0bf24de5a9ec` | `ad750580fefd` |
| `6bd238038836` | `0fa7fde19424` |
| `059646ac2026` | `b6a3b7a8f1e4` |
| `edb82ec3ab8d` | `403bac71e917` |
| `2678ddd2e36e` | `b7c5ffbde157` |
| `58bf12f0455e` | `c9162b9e0ab8` |
| `35d449e8afbd` | `580e74ca34fe` |
| `7c13c2e821c0` | `1b9500a0adbb` |
| `4db78e924deb` | `a15389911bfa` |
| `0a531ad6cead` | `2bb39a65ecd9` |
| `4c94e1c42921` | `2f9446e21229` |
| `e68c2d2e83db` | `883a103a9e56` |
| `c01a14a3f646` | `78e636f0d3b0` |
| `2d79a2d655d6` | `73351bde2528` |
| `4d0ecffbab0a` | `b48611fbab72` |
| `80d77f3e691c` | `7d98b8ac48a2` |
| `7b26e3e326f1` | `32992e5d977f` |
| `dba951309f01` | `2b5165dd2fdc` |
| `40690bb11916` | `b3b4e77d6a74` |
| `1e9d4dc3d7f4` | `1abcd3f0774c` |
| `3e2e2e0b9426` | `2a8915b25950` |
| `df9f8dec17b8` | `6ae72876fbec` |
| `08e29d3a79f5` | `791889ebae56` |
| `f5de2f9e4c04` | `4878a4db5b83` |
| `2261c462d062` | `8f83d768a2c0` |
| `c47551599134` | `a52a67621c1c` |
| `9aa279812ac8` | `ca8548f51585` |
| `5985d4dd0ca9` | `dfeaa7c890e9` |
| `8cb6b1ae573e` | `e069d63d248e` |
| `91a46b914ed1` | `3262f270cf0c` |
| `8fbb74623ae8` | `9d84e2ae2b2c` |
| `feb0f4e06e89` | `90ada92dfd04` |
| `be0d43ea4f4d` | `195909cb30e0` |
| `6334709a3ae0` | `122c912cf0ec` |
| `d896976ad661` | `1e47158d1d6a` |
| `475df91461e3` | `669598c8db81` |
| `5d9e39f72375` | `cf36f039f404` |
| `1c8a887c3722` | `424878473fe6` |
| `d21ef225d9eb` | `8fee63830ed6` |
| `0f32f3db5995` | `e5181d3aea65` |
| `07153f1ccbe7` | `40a6809c6e33` |
| `6e755abff5ed` | `f1eeb8be47fc` |
| `e19cdd1d7b3b` | `ef987a6ccc3b` |
| `d01e823f1d9a` | `0a77cd4041bd` |
| `d6c9801f8358` | `b51281e03a1d` |
| `e8d3258652a4` | `8fda4aa4340f` |
| `9c267a4459c0` | `bbc3603cc736` |
| `11360788f70b` | `f2411deb6705` |
| `97feb5d9b1b4` | `bcbfc10c756a` |
| `4aab5f729ce8` | `232e72a2b7d4` |
| `07dc00b83196` | `dbb577b4a343` |
| `db4e1c2f8149` | `c9b6d1623fbd` |
| `42a420ed91cc` | `94466aaa8a1f` |
| `cd2871094202` | `d7e25521c38e` |
| `2b66706aab96` | `a8e8c39c36e5` |
| `226763fb3ab5` | `fbce999a32df` |
| `6398fd7b4926` | `a473600e24df` |
| `4f1eca451833` | `44ec5146f371` |
| `19f30beb215b` | `cc2fa21e3447` |
| `368f909d074f` | `07fc31848613` |
| `009144fb6cbf` | `788cf4fa63c3` |
| `5b9c7dc2d104` | `25c019fdff17` |
| `1edea9fc0b65` | `97fca5732ddc` |
| `3ed5c847ab84` | `c50ea4be76ff` |
| `f5fdce3587c4` | `82f3cf2979d7` |
| `f9520cce9d64` | `989a1dd9c757` |
| `3264c3d43385` | `9d1cc6b73073` |
| `90e74d166fc6` | `6e2999928ccf` |
| `1d5dae0900a8` | `986e11274d77` |
| `48f411b7e0e9` | `32583f41656a` |
| `0bee5fffd57a` | `ded3503bf59d` |
| `ab3f4dc0baa6` | `98c0876ae741` |
