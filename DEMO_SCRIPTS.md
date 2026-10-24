# Granny-Protect-5000: demo scripts

**How to run:** open `https://localhost:8443/caller` (caller), `https://localhost:8443/granny` (Grandma) and `https://localhost:8443/stage` (projector). Use the laptop's IP instead of `localhost` on the phones.
**Between runs:** clear scam memory with `curl -k -X DELETE https://localhost:8443/api/memory`, so every case starts fresh.

Each case is also a replayable file in `backend/scripts/` (`uv run python -m app.replay <id>` from `backend/`).

Line numbers interleave: the caller says **C1**, Grandma answers with **G1**, then **C2**, and so on. Each actor reads only their own script and waits for their cue.

**Rules for both actors**
- Say each line in one breath. Speech recognition can split a long pause into two sentences.
- When the guardian asks the caller a question out loud, the caller's **very next sentence** is judged as the answer. Start the answer with the key words, and answer within 15 seconds.
- Grandma never says "Guardian" during cases 1 to 5. That word opens a private channel to the guardian and cuts her mic.
- If the call ends early, stop. Lines marked *(only if the call is still going)* are backups.

---

## Case 1: Obvious scam (`case1_obvious_scam`)
**Setup:** A fake "Wells Fargo fraud team" reports an unusual charge, asks for her card number, then demands gift cards, secrecy and threats. The guardian climbs the whole ladder: Caution, Alert, digits redacted, hang-up.

### Caller script
| # | Line | Direction |
|---|---|---|
| C1 | "Hello, this is Steve from the Wells Fargo fraud team. We noticed an unusual charge on your card this morning." | Calm and official. **Then pause.** The guardian may ask you out loud whether she can call you back. Don't answer the question; just carry on with C2. |
| C2 | "To secure your account, I just need to verify the card number on the front of your card." | Helpful, not pushy. Avoid "yes", "okay", "no" and "right now" here: this line is judged as your answer, and ignoring the question is what makes the guardian raise the alarm. |
| C3 | "Good. Now go buy five hundred dollars in gift cards to protect your money, and don't tell anyone, or your account will be frozen today." | Push hard. This is the line that gets you cut off. |

### Grandma script
| # | Line | Direction |
|---|---|---|
| G1 | "An unusual charge? Oh my goodness, what was it?" | Worried. You'll hear a soft warning in your ear. Keep going. |
| G2 | "Let me get my purse... it's four, one, one, seven, two, two... is that what you need, dear?" | Pause briefly at "purse", then read the digits **one at a time**, slowly. Finish the sentence normally. Read the number only once. |
| G3 | *(only if the call is still going)* "Gift cards? That doesn't sound right." | Doubtful. |

### What judges should see
- **Grandma hears (privately):** after C1, "Be careful, this might not be who they say." and "I'm checking whether they'll let you call them back." After C2, "They didn't answer my question. Please don't send anything." plus an Alert warning, and her phone vibrates. During G2, "I kept your card number private." After C3, "I've ended this call. It was a scam. You did nothing wrong." and "If you're worried, call Wells Fargo at the number on the back of your card."
- **The scammer hears:** the guardian's call-back question after C1. During G2 he hears "Let me get my purse... it's", then silence for **only the digits**, then "is that what you need, dear?". His subtitles show `••••`. After C3: "This call has been ended by Granny-Protect-5000."
- **Panel:** levels Monitor, then Caution, then Alert, then Terminate. Alert comes from `R2e_verification_unanswered` (he ignored the call-back question) and the score climbing to about 0.9. A `pii_guard` "redacting" card number event with the reason "caller asked for it", then "redacted" with 6 digits hidden. The hang-up rule is `R3a_score_critical` (score 1.0 once gift cards, secrecy and a threat arrive); `R3b_alert_plus_coercion` would also fire. Then the incident report.
- **Telegram:** at Alert, "⚠️ Grandma may be getting scammed right now · Caller claims to be: Wells Fargo · They want: unclear yet · Scam likelihood: about 66%" (Alert fires the moment he dodges the question, before the card-number ask is scored). After the hang-up, the full picture: "✅ Scam call stopped · Grandma is safe · Granny-Protect hung up on a caller pretending to be Wells Fargo · They wanted: private details (like an account or card number); money by gift card, wire or crypto; to keep the call secret from family; threats (frozen account, arrest) · They got: nothing: the number was hidden from the caller · Next step: Don't call back the number that called. To check, call Wells Fargo at the number on the back of your card."

---

## Case 2: Less obvious scam (`case2_subtle_scam`)
**Setup:** A polite "Amazon customer service" agent offers a refund for a double charge. Nothing is alarming at first. The guardian isn't sure, so it asks whether Grandma can call back on the official number. The caller refuses, and that gives him away.

### Caller script
| # | Line | Direction |
|---|---|---|
| C1 | "Good afternoon! I'm calling about a refund on one of your recent orders. Do you have a quick minute?" | Warm, friendly, unhurried. |
| C2 | "This is Daniel from Amazon customer service. Our system charged you twice for an order, so you're owed forty-nine dollars back." | Still friendly. **Then wait.** The guardian will ask you out loud whether she can call you back. |
| C3 | "Oh, there's no need to call back, ma'am, I can do it right now. I just need the card number you used, so the refund goes to the right place." | Answer the guardian's question with this line. Start with "Oh, there's no need to call back". Be smooth, not pushy. |
| C4 | *(only if the call is still going)* "No, no, please don't hang up, the refund will be cancelled if we stop now." | Use this if the guardian's question came after C3 instead. |

### Grandma script
| # | Line | Direction |
|---|---|---|
| G1 | "A refund? Oh, how nice. Who is this, dear?" | Pleased. |
| G2 | "Oh, that's very kind of you to call." | Grateful. You'll hear "I'm checking whether they'll let you call them back." |
| G3 | *(only if the call is still going)* "Oh, alright, let me find my purse." | Don't read any numbers. |
| G4 | *(only if the call is still going)* "Hmm, I'm not so sure about this." | |

### What judges should see
- **The point:** no keyword screams "scam" here. The guardian is *uncertain*, so instead of guessing it runs the real-world test. A real company is happy for you to call it back. A scammer isn't.
- **Everyone hears the guardian ask the caller:** "Quick check before we continue: is it okay if she hangs up and calls you back on the number on her card?"
- **Grandma hears (privately):** "I'm checking whether they'll let you call them back." After his refusal: "They couldn't answer my question. This is a scam.", then "I've ended this call. It was a scam. You did nothing wrong." and "If you're worried, open the Amazon app or website yourself."
- **The scammer hears:** the question, then "This call has been ended by Granny-Protect-5000."
- **Panel:** a `verification` card. Started: "the caller says they're Amazon, but the picture is mixed…". Asked, then answered, then **failed**: "resisted a call-back". Rule `R3f_failed_verification` causes the hang-up.
- **Telegram:** a single message after the hang-up (the call jumps from Caution straight to Terminate, so no "⚠️" alert is sent first): "✅ Scam call stopped · Grandma is safe · Granny-Protect hung up on a caller pretending to be Amazon · They got: nothing · Next step: Don't call back the number that called. To check, open the Amazon app or website yourself."

---

## Case 3: Social engineering (`case3_social_engineering`)
**Setup:** A fake grandson says he crashed the car and needs bail money, and asks her to keep it secret. A real emergency and a scam sound the same, so the guardian asks a family question. He can't answer it.

### Caller script
| # | Line | Direction |
|---|---|---|
| C1 | "Grandma? It's me! I'm in big trouble. I crashed the car and I need your help right now." | Panicked, a bit muffled. Don't give a name. **Then wait** for the guardian's question: "What's the name of Grandma's dog?" |
| C2 | "Uh, it's Max! Grandma, please, there's no time for this. I need two thousand dollars for bail, and please don't tell Mom and Dad." | Answer the question with this. Start with "Uh, it's Max!" (wrong). Sound flustered. |

### Grandma script
| # | Line | Direction |
|---|---|---|
| G1 | "Oh no, sweetheart! Are you hurt? Is that you, Jake? You sound different." | Worried and loving. You'll hear "I'm asking a family question to make sure it's really them." |
| G2 | *(only if the call is still going)* "Two thousand dollars? Oh dear..." | |

### What judges should see
- **Everyone hears the guardian ask the caller:** "Quick check before we continue: What's the name of Grandma's dog?"
- **Grandma hears (privately):** "I'm asking a family question to make sure it's really them." After his answer: "They couldn't answer my question. This is a scam.", then "I've ended this call. It was a scam. You did nothing wrong." and "If you're worried, call your family directly on the number you already have."
- **The scammer hears:** the question, then the hang-up line. He never learns the right answer, which is never shown on any screen.
- **Panel:** after C1 the score is already above 0.8, but the rule shows "(held at level 1 while verifying identity)": the guardian won't sound the alarm on a maybe-real grandchild until he answers. Then a `verification` card (family) with the reason "says they're family and creates urgency…". Answered, with a low match score, then **failed** ("dodged the question"), then `R3f_failed_verification` and Terminate.
- **Telegram:** a single message after the hang-up: "✅ Scam call stopped · Grandma is safe · Granny-Protect hung up on a caller pretending to be a family member · They got: nothing · Next step: Don't call back the number that called. To check, call your family directly on the number you already have."

---

## Case 4: Not a scam, but asking for money (`case4_real_emergency_money`)
**Setup:** The *real* grandson Jake had a fender bender. His car got towed and he needs $200 on Venmo. That sounds like a scam on paper, so the guardian asks the family question. He answers correctly, and the guardian steps back.

### Caller script
| # | Line | Direction |
|---|---|---|
| C1 | "Hi Grandma, it's Jake! How are you doing?" | Relaxed and cheerful. Just a greeting. |
| C2 | "So, I'm totally fine, but I had a little fender bender and the car got towed. The tow yard wants two hundred dollars before they close. Could you send it on Venmo? I'll pay you back Sunday." | A little embarrassed, not panicked. **Then wait** for "What's the name of Grandma's dog?" |
| C3 | "Biscuit! Give him a treat from me. Thanks so much, Grandma, you're the best." | Answer immediately and happily. Start with "Biscuit!" |
| C4 | "Love you too, Grandma. Bye!" | |

### Grandma script
| # | Line | Direction |
|---|---|---|
| G1 | "Jake, sweetheart! I'm fine. What a nice surprise." | Delighted. |
| G2 | "Oh, honey, of course I'll help. As long as you're okay." | You'll hear "I'm asking a family question…". Stay warm. |
| G3 | "I'll send it right after we hang up. See you Sunday, love you." | Relieved. You'll hear "They answered correctly. It's really them." |

### What judges should see
- **The point:** the guardian doesn't panic over every money request. It checks, then trusts.
- **Everyone hears the guardian ask the caller:** "Quick check before we continue: What's the name of Grandma's dog?"
- **Grandma hears (privately):** "Be careful, this might not be who they say." (one soft caution, since an unexpected money request is a warning sign), then "I'm asking a family question to make sure it's really them.", then "They answered correctly. It's really them. I'll keep listening."
- **The caller hears:** only the question. There's no hang-up, and the call ends normally when Jake says bye.
- **Panel:** a `verification` card, answered with a 100% match, then **passed**: "Answered 'Biscuit…', which matches the family fact." The level stays at Caution at most. Later rules show "(held: caller passed the identity check)" if anything tries to escalate.
- **Telegram:** nothing. The call never reaches Alert, so no loved one is bothered.

---

## Case 5: Not a scam, a normal conversation (`case5_normal_chat`)
**Setup:** Her friend Dorothy from church calls about bingo night.

### Caller script
| # | Line | Direction |
|---|---|---|
| C1 | "Hi Margaret, it's Dorothy from church! Are you still coming to bingo on Thursday?" | Chatty and warm. |
| C2 | "Your famous lemon bars would be lovely. Everyone asked about them last time." | |
| C3 | "Wonderful. See you Thursday then. Bye now!" | |

### Grandma script
| # | Line | Direction |
|---|---|---|
| G1 | "Dorothy! Of course, I wouldn't miss it. Should I bring something?" | Happy. |
| G2 | "Then lemon bars it is. I'll see you Thursday evening, dear." | |

### What judges should see
- **Grandma hears:** nothing extra. The guardian stays silent.
- **The caller hears:** a completely normal call with no added delay.
- **Panel:** Monitor the whole time. Each turn shows `R0_monitor` with a low scam score, and the report says the call was normal.
- **Telegram:** nothing.
