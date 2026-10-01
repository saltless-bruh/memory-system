# History rewrites — 2026-10-01

This branch had not yet been pushed past `6f143d7` when its history was rewritten
twice on 2026-10-01, each time because a pre-push leak audit found private material
in commits about to become public. `origin` is public and the private vault must
never reach it.

**First rewrite** (81 commits from `ae613d0` onward). Three lines in
`docs/superpowers/handoffs/2026-09-09-agy-batch-3-pilot-h3.md` were a verbatim
excerpt of a private vault page and were replaced with a placeholder. The author's
personal name was replaced with the GitHub handle in the 13 legacy proposals added
by the same commit.

**Second rewrite** (82 commits from `0a1d203` onward). The audit of the first
rewrite's head found a second excerpt: three lines in
`docs/superpowers/handoffs/2026-09-13-agy-batch-3b-h3.md`, replaced with the same
placeholder. By the owner's ruling, the team lead's chat feedback, which the legacy
proposals quoted word for word, was removed in the same pass: 11 quote tables became
a one-line note and 63 inline quotes became a neutral label. The proposals
themselves stay.

Nothing else changed in either rewrite: every other file has the same tree, and
every commit keeps its author, dates and message. The `wiki/` tree is byte-identical
throughout.

Older notes, commit messages and the gitignored ledger cite earlier IDs; this table
resolves them. Neither earlier set of IDs was ever published to a remote. The middle
column existed only locally, for a few hours. The last row is the commit that first
recorded this table, so it has no original ID.

| original | after first rewrite | final |
|---|---|---|
| `ae613d0c4b25` | `0a1d203bc01e` | `8fa3eb1890d1` |
| `d0d1426a48de` | `60b860588192` | `b514f2c44744` |
| `d043f13ff249` | `78aad4e00ad2` | `6d2d9acd71ed` |
| `57fd812f0f78` | `8487dfc9efc8` | `83242b8b3778` |
| `016ed43fba0d` | `a3dcd16a207a` | `5a21f7716268` |
| `4895739e07ef` | `59d781cf2f73` | `f069574a9373` |
| `8d0beb74d2d2` | `7fcccc0288d8` | `df4fe329f08b` |
| `6bd238038836` | `0fa7fde19424` | `2157cd55cc1a` |
| `35d449e8afbd` | `580e74ca34fe` | `47eed7c4712c` |
| `0bf24de5a9ec` | `ad750580fefd` | `9d5dcde86e31` |
| `7c13c2e821c0` | `1b9500a0adbb` | `cba843bb183d` |
| `2441706c2422` | `7c502237afee` | `7377b80e263a` |
| `2678ddd2e36e` | `b7c5ffbde157` | `49c9c5c41753` |
| `4db78e924deb` | `a15389911bfa` | `b87fb5a86d93` |
| `059646ac2026` | `b6a3b7a8f1e4` | `5d55bc6cfbb4` |
| `58bf12f0455e` | `c9162b9e0ab8` | `386ce05c6b8a` |
| `0a531ad6cead` | `2bb39a65ecd9` | `bb7d0e8cd274` |
| `7f5cf98bc443` | `3a19a6f80ce8` | `f75e619368c5` |
| `edb82ec3ab8d` | `403bac71e917` | `ba2a57062514` |
| `4c94e1c42921` | `2f9446e21229` | `f2ae6056b718` |
| `e68c2d2e83db` | `883a103a9e56` | `b61bef7098d0` |
| `2d79a2d655d6` | `73351bde2528` | `54dea8a2fb3f` |
| `1e9d4dc3d7f4` | `1abcd3f0774c` | `aa278af7f229` |
| `df9f8dec17b8` | `6ae72876fbec` | `04f273b91f6b` |
| `6334709a3ae0` | `122c912cf0ec` | `c73a4d4a103f` |
| `db4e1c2f8149` | `c9b6d1623fbd` | `ceefbee96eb3` |
| `7b26e3e326f1` | `32992e5d977f` | `1f1c3c36df00` |
| `40690bb11916` | `b3b4e77d6a74` | `773222c6b531` |
| `f5de2f9e4c04` | `4878a4db5b83` | `293d6991ce08` |
| `be0d43ea4f4d` | `195909cb30e0` | `075c8b1369ae` |
| `d21ef225d9eb` | `8fee63830ed6` | `26db35f16c7c` |
| `42a420ed91cc` | `94466aaa8a1f` | `c594a54ed6d2` |
| `8cb6b1ae573e` | `e069d63d248e` | `cc254407052e` |
| `91a46b914ed1` | `3262f270cf0c` | `72ec67bcf788` |
| `8fbb74623ae8` | `9d84e2ae2b2c` | `b39a48a35d00` |
| `cd2871094202` | `d7e25521c38e` | `256d8b32d100` |
| `dba951309f01` | `2b5165dd2fdc` | `ef3431581b67` |
| `3e2e2e0b9426` | `2a8915b25950` | `45ab0f785635` |
| `c47551599134` | `a52a67621c1c` | `cab8c08ba8f6` |
| `feb0f4e06e89` | `90ada92dfd04` | `0fb7aea79cf8` |
| `475df91461e3` | `669598c8db81` | `e0327ac28d08` |
| `e19cdd1d7b3b` | `ef987a6ccc3b` | `1f79d70a538e` |
| `2b66706aab96` | `a8e8c39c36e5` | `2066fc93ba9c` |
| `4d0ecffbab0a` | `b48611fbab72` | `6e09b4e1a1b6` |
| `9aa279812ac8` | `ca8548f51585` | `b2ea4266d035` |
| `d896976ad661` | `1e47158d1d6a` | `6b1959bc00f5` |
| `226763fb3ab5` | `fbce999a32df` | `7ee63fc700db` |
| `c01a14a3f646` | `78e636f0d3b0` | `a42c4d4b8b49` |
| `80d77f3e691c` | `7d98b8ac48a2` | `81714842e04a` |
| `08e29d3a79f5` | `791889ebae56` | `11c1bb0e6d0e` |
| `2261c462d062` | `8f83d768a2c0` | `f0bad1f8b7ae` |
| `5985d4dd0ca9` | `dfeaa7c890e9` | `3e6e0e91eb7b` |
| `5d9e39f72375` | `cf36f039f404` | `0c31406b4991` |
| `1c8a887c3722` | `424878473fe6` | `bdb39fdbf6a7` |
| `97feb5d9b1b4` | `bcbfc10c756a` | `2de48f59ea1d` |
| `4aab5f729ce8` | `232e72a2b7d4` | `7c4d01e31d41` |
| `6398fd7b4926` | `a473600e24df` | `5743d4066b4c` |
| `0f32f3db5995` | `e5181d3aea65` | `6dd5781f78af` |
| `07153f1ccbe7` | `40a6809c6e33` | `d18a65c32fb6` |
| `6e755abff5ed` | `f1eeb8be47fc` | `66092296009a` |
| `d01e823f1d9a` | `0a77cd4041bd` | `377fc7027613` |
| `d6c9801f8358` | `b51281e03a1d` | `80fa49eb3930` |
| `e8d3258652a4` | `8fda4aa4340f` | `4dd4544cddec` |
| `9c267a4459c0` | `bbc3603cc736` | `fc28bcf6c210` |
| `11360788f70b` | `f2411deb6705` | `2fcf739fdf7d` |
| `07dc00b83196` | `dbb577b4a343` | `85ae85f7439e` |
| `4f1eca451833` | `44ec5146f371` | `ea1ec29de9b9` |
| `19f30beb215b` | `cc2fa21e3447` | `6612e429851b` |
| `368f909d074f` | `07fc31848613` | `d13cb7811fc4` |
| `5b9c7dc2d104` | `25c019fdff17` | `0db3a7a28221` |
| `90e74d166fc6` | `6e2999928ccf` | `5a675390d288` |
| `009144fb6cbf` | `788cf4fa63c3` | `cc413c194ab7` |
| `1edea9fc0b65` | `97fca5732ddc` | `0d7cc9760109` |
| `3ed5c847ab84` | `c50ea4be76ff` | `948aa34cd256` |
| `f5fdce3587c4` | `82f3cf2979d7` | `556c98131e1a` |
| `f9520cce9d64` | `989a1dd9c757` | `6f846c5d45f4` |
| `3264c3d43385` | `9d1cc6b73073` | `e548ea0bc287` |
| `1d5dae0900a8` | `986e11274d77` | `7b69b0114e9d` |
| `48f411b7e0e9` | `32583f41656a` | `c99eb3ac868f` |
| `0bee5fffd57a` | `ded3503bf59d` | `3050ca40e93d` |
| `ab3f4dc0baa6` | `98c0876ae741` | `a70a60738a69` |
| — | `cd5a2a12b9ac` | `488e35dcbac0` |
