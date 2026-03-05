# **Pain Point Scanner: Hypothesis & Classification Schema**

**Version:** 1.0 **Product:** TryLinguals — customizable multilingual children's books **Purpose:** This file defines what the scanner is testing and how it classifies what it finds. The classifier agent references this file on every run. Do not modify categories mid-run. Update versioning when schema changes so reports remain comparable over time.

---

## **Core Product Hypothesis**

TryLinguals' value is not "multilingual books." It is: a parent raising a child in any specific combination of languages can get a book made for exactly their family. The families with no pre-made product available anywhere are the most motivated early adopters. The scanner is calibrated to find them.

---

## **The Five Validation Hypotheses**

**H1: Multilingual parenting is an active, recurring pain point** Parents are not passively interested. They are actively struggling and seeking solutions in community spaces. Confirmed when: frustration posts, resource requests, and language maintenance questions appear at consistent weekly volume across multiple communities. Disconfirmed when: discussions are academic or theoretical rather than practical and urgent.

**H2: Books are part of the solution parents actually want** Among all possible solutions (apps, tutors, cartoons, games), books appear in parent discussions as a desired format. Confirmed when: parents request, recommend, or purchase books specifically. Disconfirmed when: books are absent from discussions even when resources are requested.

**H3: Parents need content spanning multiple languages simultaneously** The demand is not just for content in one heritage language. Parents want resources that hold two or three languages at once. Confirmed when: posts name specific language combinations, ask for bilingual or trilingual books, or express frustration that existing books only cover one language. Disconfirmed when: parents seek single-language content exclusively.

**H4: Personalization increases perceived value** Parents respond to the idea of books customized to their child or their specific language combination, beyond generic bilingual products. Confirmed when: any mention of child's name in books, custom language combinations, or explicit requests for personalized content. Note: even occasional signal here is meaningful given low base rate. Disconfirmed when: parents show no preference between generic and personalized.

**H5: Parents demonstrate willingness to pay** Parents already spend money on bilingual or multilingual educational materials. Confirmed when: posts reference purchasing books, asking for price recommendations, Etsy or Amazon bilingual book purchases, or budget discussions. Disconfirmed when: all resource requests trend toward free content only.

---

## **Classification Schema**

Every post and comment the scanner processes gets classified against the following fields. All fields are required. Use "unknown" when a field cannot be determined from the content.

**Pain Type** (select one primary, one secondary if applicable)

* LANGUAGE\_MAINTENANCE: keeping a child active in a language they are losing or resisting  
* RESOURCE\_REQUEST: asking for books, apps, tools, or any educational material  
* BOOK\_REQUEST: specifically requesting books (subset of resource request, flag separately)  
* PERSONALIZATION\_REQUEST: asking for custom or personalized content  
* PURCHASE\_DISCUSSION: discussing cost, where to buy, or sharing a purchase  
* FRUSTRATION\_GAP: expressing that nothing exists for their specific situation  
* SUCCESS\_SHARE: sharing something that worked (inverse signal, still valuable)  
* RECOMMENDATION: recommending a specific product or resource to others

**Child Age Range** (select one)

* 0-2  
* 2-4  
* 4-6  
* 6-8  
* 8+  
* unknown

TryLinguals current target is 2-6. Flag anything in that range as IN\_TARGET.

**Language Combination** (extract all languages mentioned, note count)

* Record every language named in the post or comment  
* Flag posts naming three or more languages as TRILINGUAL\_SIGNAL  
* Flag posts naming an unusual or underserved combination as UNDERSERVED\_COMBO  
* Common combinations (English-Spanish, English-French, English-Mandarin) are noted but not prioritized. These families have existing options.

**Emotion** (select one)

* FRUSTRATION: something is not working or does not exist  
* CONFUSION: parent does not know what to do or where to look  
* CURIOSITY: exploring options without urgency  
* SATISFACTION: something is working well  
* URGENCY: time-sensitive or high-stakes situation

Frustration and urgency are the strongest signals. Weight them accordingly in reports.

**Hypothesis Match** (select all that apply)

* H1, H2, H3, H4, H5  
* A post can match multiple hypotheses. Record all matches.

**Reachable Parent Flag** Mark TRUE when all of the following are present:

* Post is a question or request (not a recommendation or complaint with no ask)  
* Child age is in 0-6 range or unknown  
* Post is less than 30 days old  
* Post is from a non-commercial account

These posts become the Week 2 outreach shortlist. Respond to threads publicly. Do not DM.

---

## **Signal vs. Noise Rules**

Count as signal: posts with 3+ upvotes OR 2+ comments engaging with the topic. Treat as noise: zero-engagement posts, posts from accounts with clear commercial intent, posts in languages other than English unless the language itself is the subject.

A hypothesis is considered validated when: 20+ posts match it in a single weekly run across at least two different communities. A hypothesis is considered weak when: fewer than 5 posts match it across all communities in a weekly run.

---

## **What the Weekly Report Must Contain**

1. Hypothesis validation status for each of the five hypotheses (confirmed, weak, insufficient data)  
2. Top 5 pain points by frequency and engagement weight  
3. Language pair frequency table: which combinations appeared most, which were flagged as underserved  
4. Reachable parent shortlist: thread URL, pain type, child age, language combination, date  
5. One recommended product or validation action based on the week's findings
