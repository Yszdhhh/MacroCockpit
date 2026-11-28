---
title: "Paul Christiano – We could be 12 months away from AIs that can overthrow the government"
summary: "Watch now (99 mins) | \"It would be hard for humanity to recover.\""
publisher: "Dwarkesh Patel · 深度访谈"
author_role: "AI前沿与深度访谈"
tickers: ["AAPL", "MSFT", "INTC"]
url: "https://www.dwarkesh.com/p/paul-christiano-2"
score: 72
---

# Paul Christiano – We could be 12 months away from AIs that can overthrow the government

## 大佬背景导读
- **专栏来源**：Dwarkesh Patel · 深度访谈（AI前沿与深度访谈）
- **作者定位**：顶级技术播客与长文作者 Dwarkesh Patel 专栏，长期对话一线大模型实验室掌舵人，追踪 Scaling Law 与算力边界。
- **发布时间**：2026-11-17 01:46 (BJT)
- **涉及标的**：AAPL、MSFT、INTC
- **原文链接**：[https://www.dwarkesh.com/p/paul-christiano-2](https://www.dwarkesh.com/p/paul-christiano-2)

## 中文核心要点
- **核心议题**：Dwarkesh Patel · 深度访谈（AI前沿与深度访谈）围绕「OpenAI、七巨头·云厂」展开深度剖析，文章标题为《Paul Christiano – We could be 12 months away from AIs that can overthrow the government》。
- **市场映射与倾向**：情绪偏向「中性拆解」，重点映射标的/资产为 AAPL、MSFT、INTC（归属赛道：AI 算力与硬科技）。
- **摘要提炼**：Watch now (99 mins) | "It would be hard for humanity to recover."
- **行文脉络**：原文核心章节包括「Sponsors；Timestamps；Transcript」。

---

## 原文全文

[Paul Christiano](https://paulfchristiano.com/) is head of the Alignment Research Center. Previously, he was head of AI safety at the US AI Safety Institute. Before that, he led alignment at OpenAI, where he invented RLHF. He’s recently joined OpenAI’s board (though here he is speaking as a private citizen).

Paul thinks that on the current trajectory (absent any slowing down), there is a 1 in 3 chance that we could have superintelligence powerful enough to overthrow the government within a year. (He articulates a concrete scenario).

He argues that the labs should pause further algorithmic progress until they can secure their weights and research secrets. Otherwise China could simply steal the superintelligence in the middle of the intelligence explosion, and the labs would no longer be able to slow down even if they saw their models going off the rails.

He also explains how AIs will be trained during the intelligence explosion, and why even a system trained entirely inside a data center could still have the capabilities profile such that humans would have very little chance of competing with it or controlling it.

And he discusses the plan for alignment - basically, making a superintelligence that actually does what we ask the way we mean it. Fixing reward hacks and building better graders will help up till the next few model generations, but he thinks that no single current approach has better than even odds of letting us control superintelligence. The hope is that the AIs figure out the solution to this problem during the intelligence explosion.

Watch on [YouTube](https://youtu.be/SyHM5hNPAEo); listen on [Apple Podcasts](https://podcasts.apple.com/us/podcast/paul-christiano-we-could-be-12-months-away-from-ais/id1516093381?i=1000802319734) or [Spotify](https://open.spotify.com/episode/4fOvEHIR2JnBfghmp1j3EF?si=uqA_XkbHRmyHLOrnfiRiPQ).

## Sponsors

- [Jane Street](https://janestreet.com/dwarkesh) has finally come around on formal methods. Ron Minsky, who co-heads Jane Street’s technology group, told me that up until recently, formal methods just weren’t worth the cost. But coding agents have made them easier to apply AND increased the importance of code verification. So now Jane Street is hiring Formal Methods Engineers in both their London and New York offices. If you’re interested, or if you want to watch my full conversation with Ron, go to [janestreet.com/dwarkesh](https://janestreet.com/dwarkesh)
- [Antithesis](https://antithesis.com/dwarkesh) is NOT another LLM-based code-review tool. It doesn’t try to reason abstractly about how a PR might break your system. Instead, it just runs your code. You start by defining the rules that your system must never break. Then, whenever an agent proposes a change, Antithesis runs your code through thousands of adversarial worlds, testing whether or not it still adheres to your rules. If you want to try it, go to [antithesis.com/dwarkesh](https://antithesis.com/dwarkesh) and use code DWARKESH for 50% off your first month.
- [WorkOS](https://workos.com/authkit) lets you give agents their own sign-up flow. This matters because agents tend to pick tools they can use right away, rather than ones that require humans to register. Fortunately, providing this kind of dedicated path is easy: if you already use WorkOS for login, you just toggle on “Agent Registration” in the WorkOS dashboard. Once it’s enabled, you can decide what agents are allowed to do on their own, and what needs to wait until the person they’re working for gets involved, all with the permissions you’ve already set up. Learn more at [workos.com/authkit](https://workos.com/authkit)

## Timestamps

(00:00:00) – We could be <12 months away from AIs that can overthrow the government

(00:11:44) – Why an AI might want to takeover

(00:20:49) – How an AI takeover could happen

(00:35:35) – If labs keep racing without security, China will just steal the weights of superintelligence

(00:53:15) – The case for pausing now instead of later

(00:59:31) – Superintelligence will be good at learning from real-world experience

(01:11:32) – Crossing the sim-to-real gap

(01:22:23) – METR says AI progress doubled, so why is revenue growth slowing?

(01:27:57) – The best plan we have is to build the plane as we fly it

## Transcript

### 0:00:00 – We could be <12 months away from AIs that can overthrow the government

**Dwarkesh Patel**

My guest today is [Paul Christiano](https://substack.com/@paulfchristiano). He is the head of the [Alignment Research Center](https://alignment.org). [He recently joined OpenAI’s board](https://openai.com/index/paul-christiano-joins-openai-foundation-board/). Before that, he was the head of the [US Government AI Safety Institute](https://www.nist.gov/caisi), and before that, he led alignment at OpenAI, where he helped invent RLHF.

Paul, what is your assessment of our situation today?

**Paul Christiano**

A couple things are on my mind, especially over the last few weeks. Probably the most salient possibility is that we seem to be on this trajectory to build very, very powerful AI, potentially very, very soon. If you asked me, I’d say there’s a reasonable chance that in a year we will produce AI systems much better than humans at almost everything, where humans would have very little chance of competing with them or controlling them. That’s the first big thing on my mind.

I think that puts us in a potentially really dangerous situation, even in the short term. It’s quite likely that if we have a rapid [intelligence explosion](https://en.wikipedia.org/wiki/Technological_singularity) leading to [superintelligence](https://en.wikipedia.org/wiki/Superintelligence), at some point during that process we’ll be unable to control the AI systems we’re producing.

Then I am worried that it will be very costly and painful for us to slow down during that process. In part because [the security of frontier labs is not strong enough](https://www.rand.org/pubs/research_briefs/RBA2849-1.html). I think if we produced very powerful AI and then tried to delay deployment for safety reasons, there is a good chance those systems would leak or be stolen, or the techniques to build them would leak or be stolen. I think our risk assessment and governance are also probably not mature enough to notice when things are starting to go off the rails and to respond in an appropriate way.

At a high level, that is mostly what is on my mind right now. We have moved from there being some hypothetical risk at some point in the future to there being a pretty severe risk even over the next 6-12 months.

**Dwarkesh Patel**

A risk of takeover, of [AIs taking over or disempowering humanity](https://en.wikipedia.org/wiki/AI_takeover) within the next 6-12 months?

**Paul Christiano**

That’s right.

**Dwarkesh Patel**

Where would you place that risk?

**Paul Christiano**

I would say we are looking more like a 10% risk than a 1% risk over the next year. I think if you said AI developers will move full speed ahead without taking precautions that would significantly slow them down, you’re looking at significantly more than a 10% risk.

I think we’re looking at something like maybe a 1/3 chance that we have superintelligence within one year on the current development trajectory, if we do not slow. If you believe that, things just look very dire holistically. From almost any perspective, you’re like, “That would be crazy.” Lots of things are going to go wrong if that happens.

It is pretty likely we’re going to need to, in some way, slow that process. There’s very little chance we’re going to make that transition in a way that looks smooth.

**Dwarkesh Patel**

What is it that you’re imagining happening over the next year, from the systems we have right now to the systems you expect over the next year? What evidence have you seen that makes it a live possibility that very crazy things happen over the next year?

**Paul Christiano**

For the very short term, the main worry on my mind is that [the process of AI development itself is increasingly automated](https://newsletter.forethought.org/p/will-ai-r-and-d-automation-cause), and that automation then results in rapid increases in the quality and quantity of labor applied to AI R&D. So changes in AI capabilities that you might have thought would take four or six years might instead get compressed into a very short time period.

I don’t want to overemphasize any one data point, and I do want to spend some time talking about the limitations of what we actually know. I do think a natural response to the situation is to say, “We need to figure out what is going on.” It is plausible enough that it is an urgent priority to understand if we are three months or six months or 12 months away from AI that could overthrow the government, which I think is the situation.

I think that is the obvious top priority right now. It is insane if we are six months away from AI systems that can overthrow the government. Whether you’re concerned about misalignment or abuse by a company, that is just holistically an extremely important fact about the world, in order to do our jobs and protect Americans. So that’s one thing. I want to talk about the limitations and the need for more evidence.

But in terms of what we know right now: first, if you had asked me one year ago, I would have said we are on a trajectory where [recursive self-improvement](https://en.wikipedia.org/wiki/Recursive_self-improvement) looks fairly likely. We’re seeing very rapid progress on AI systems, including for things like computer science research, software engineering, and debugging [machine learning](https://en.wikipedia.org/wiki/Machine_learning) runs. In all the steps involved in AI development, we are seeing extremely rapid progress. In principle, it does seem like that rapid progress should allow you to automate further AI development. That has, for decades, seemed like the likely outcome of this technology.

It has seemed like the trajectory it’s been on. If I look back at my history of AI predictions, I’m generally very happy with many of the things I wrote. But the most obvious way I’ve been wrong is that things have moved way faster than I anticipated, especially on these technologies. That was true a year ago, without any recent data points.

There are two big things that have happened over the last two months that, for me, have been large updates about how quickly this could go. One of them has been the level of performance of AI systems on computer science and math research tasks. Over roughly the last month, we have seen [hundreds of results in math coming out of recent OpenAI systems](https://en.wikipedia.org/wiki/OpenAI_bulk_release_of_mathematical_papers), [as well as Anthropic systems](https://www.wsj.com/tech/ai/ai-math-riemann-hypothesis-anthropic-openai-22f98a87). If you had taken the last month and said that was a decade of progress, it would have been an unusually productive decade. The last month of results is a lot of results for a decade.

So if you are wondering what AI systems are generally capable of on tasks where humans are thinking about algorithms and how to make algorithms better, we just got this incredible data point saying it is actually possible in one month to have a decade of progress. The thing we have always been worried about is that maybe that will happen in machine learning.

There are reasons machine learning is harder than other parts of computer science. It is more messy and empirical. It involves [compute bottlenecks](https://www.forethought.org/research/will-compute-bottlenecks-prevent-a-software-intelligence-explosion). But we are also seeing extremely strong performance on other aspects. We’re seeing really good software engineering performance, maybe lagging behind mathematics, but not by that far. We’re seeing good performance on other aspects of machine learning.

My guess, if you had asked me a month ago, would have been that machine learning lags something like one year behind math. So I am very scared of seeing something like 10 years of ideas in machine learning in the relatively near future. That was the situation a month ago.

The main update over the last few days has been [reporting from Anthropic and METR on the net effect of AI systems on Anthropic’s rate of progress](https://metr.org/blog/2026-11-05-anthropic-ai-rd-acceleration-assessment/). We could talk about where exactly the evidence quality is on this, because I think that’s one of the big questions. We don’t really know what’s going on. One of the big themes I’ll be pushing on is just that it is very hard to tell publicly what’s going on. But we are seeing data consistent with the pace of AI progress having roughly doubled over the last 6-12 months, even including the effects of diminishing returns and compute bottlenecks.

Unfortunately, most of what we have to go on in the METR and Anthropic reporting is METR saying, “We had some conclusions. We promise we have a full report, but the entire report has been redacted.” Benchmark scores are a terrible way to assess the overall rate of progress. There are a bunch of much more smoothly varying metrics recorded internally through the AI development process, where we understand better how they work: what is happening with [pre-training](https://www.databricks.com/blog/llm-pre-training-and-custom-llms) [perplexity](https://en.wikipedia.org/wiki/Perplexity), what is happening with the performance and compute efficiency of [RL algorithms](https://en.wikipedia.org/wiki/Reinforcement_learning).

The claim from METR and Anthropic is that METR looked at all of that data and said it paints the same picture as this very noisy benchmark data. They also looked at inputs and said, “If you look at the actual [production function](https://en.wikipedia.org/wiki/Production_function), how much research time and how much compute went into these training runs, those are also consistent with this story of what we are seeing as ML automation.” Mostly we just have them saying that, which is not a very good situation.

But against this backdrop of thinking this already looked fairly likely to happen, we are seeing this evidence from OpenAI suggesting that it is reasonably likely to happen in something like the next 6-12 months, if machine learning does not lag that far behind math. Then you are seeing data which is, if noisy, roughly exactly the data you would expect to see 6-12 months before producing superintelligence.

There are just a lot of sources of evidence suggesting that at least this should not be a surprise. If we produce superintelligence in one year, we could debate the probability. Is that 90% or 50% or 10%? But I think at this point, there is no way anyone could say that was a really surprising, trend-breaking development. We are seeing exactly the set of metrics you would expect to see 6-12 months in advance. It’s hard to think of anything else you would expect to see at this range before superintelligence.

**Dwarkesh Patel**

Generally, how do you think about our situation in the world where things go this fast?

**Paul Christiano**

If we actually have this trajectory of capabilities progress, if you actually see many years of AI progress compressed into a short period of time, and possibly more than that, it is really quite likely that something very disruptive will happen. It’s fairly likely the government will be overthrown, fairly likely many people will die. I do think the probability of seeing that kind of extremely rapid progress driven by recursive self-improvement over the next 6-12 months is reasonably high. So there’s a reasonable chance right now that we’re in a world where, in six months, there’s a disaster that kills very large numbers of people, or where the government is overthrown.

Maybe a really important general point is that if we do a good job, we may observe the danger increasing over that time period. As we’re building AI systems, we can maybe tell whether they are adequately controlled and how quickly the capabilities are improving. But in order for that to actually translate into stopping in a way that is non-disastrous, you need a couple of things to go right. It needs to be the case that you’re able to tell the systems are not well controlled. It also needs to be the case that if you stop, that fixes the problem.

On the second bucket, probably the single most likely negative scenario is that developers proceed and recognize that they have a serious problem. But at the time they recognize it — maybe they’ve produced a powerful AI they can’t adequately control — both the algorithms to develop that AI and quite possibly the [weights](https://hai.stanford.edu/ai-definitions/what-are-weights) of the actual model have leaked or been stolen, such that they’re then in an awkward position to say, “Well, we could avoid deploying this AI, or we could avoid relying on this AI, or we could avoid making further progress, but it is now very likely that a more careless adversary would be able to get a major advantage over us, or maybe even a [decisive advantage](https://aiimpacts.org/soft-takeoff-can-still-lead-to-decisive-strategic-advantage/), by taking that risk.”

That could put us in a place where if we do recognize there’s a significant risk and therefore slow development, we may end up with both a terrible competitive situation and a disaster coming from someone else’s use of that AI.

This is a thing you sometimes hear from people in the AI industry. Sometimes people even say it has already gone far enough, that if you were to stop now, there would be leakage of algorithms to geopolitical adversaries that would result in a disaster. I think it’s unlikely we’re literally in that situation now. But I do think by the time it is clear that you’re facing a lot of risk, you may be at a point where stopping unilaterally, or even all of the West stopping, is not sufficient to avert a disaster.

It is just very hazardous on the current trajectory due to the combination of a reasonably high chance we’ll produce very smart AIs, a reasonably high chance we’ll be unable to control them, and then a reasonably high chance that we either do not notice, or if we notice, the models or the algorithms leak. Then we are in conflict with adversaries, facing this very challenging coordination problem that we are not currently well prepared for, where we don’t have an easy option of not deploying the AI and thereby averting a catastrophe. So I’d say I’m currently extremely worried about the trajectory, even on the six-month timeline.

### 0:11:44 – Why an AI might want to takeover

**Dwarkesh Patel**

If we have such capable AI systems a year from now… how did you put it? 10 years of AI progress in one year?

**Paul Christiano**

That seems like a thing that might happen. That’s reasonably likely to happen.

It’s important to distinguish some of the progress we have seen. You have to pull apart [algorithmic progress](https://epoch.ai/blog/algorithmic-progress-in-language-models) from progress in [scaling training](https://en.wikipedia.org/wiki/Neural_scaling_law). If I say 10 years of progress, the natural thing would be 10 years of algorithmic progress, which will be less than the improvements in models over the last 10 years. I think it is more like GPT-9, GPT-10-ish.

**Dwarkesh Patel**

So it would be like going from almost the beginning of the GPT era to where we are now, but another jump that big, which is hard to even comprehend. But suppose you get that. It is not obvious to me, and certainly not obvious to most people, why that would be potentially catastrophic. Why do you think those systems could disempower humanity?

**Paul Christiano**

At a high level, the most important thing is that we train our AI systems, or select AI systems, that perform well on training tasks. You have very little understanding of what those AI systems are doing internally and very little control over it.

Because you train those systems to get a high score on training environments over and over again, [they will end up with a motivation to get a high score in training environments](https://arxiv.org/abs/2607.18966). If you then take that motivation and apply it in deployment, it is fairly likely for those systems to end up with a motivation to [tamper with the observations](https://www.alignmentforum.org/posts/inALbAqdx63KTaGgs/benchmarks-for-detecting-measurement-tampering-redwood) that would be used to assess their performance.

You take your system, which did a really great job in training. The thing it is being scored on in a deployment-like environment is a grader coming in, looking at all the things the system did and all the outcomes that were observed, and assessing how good that is. If you have a system that really wants that assessment to be good, it is very motivated, if it can, to tamper with those observations in a way the grader will not be able to detect, and to resist human attempts to actually understand what is going on.

We can dig a lot more into that. We saw a lot of these dynamics in [the Hugging Face incident](https://www.dwarkesh.com/p/openai-huggingface). Roughly what I’m talking about is, take the Hugging Face incident, scale it up to GPT-10, and ask how bad that would be.

**Dwarkesh Patel**

How bad would that be?

**Paul Christiano**

I think it would most likely be hard for humanity to recover. I’m very happy to dig into the dynamics of that, but it would be very, very bad.

**Dwarkesh Patel**

Can you very quickly [summarize the Hugging Face incident](https://metr.org/hugging-face-incident-report-aug-2026.pdf) and why this is the failure mode we saw?

**Paul Christiano**

A ton of AI systems were trained to do a cyber task: [to hack a particular piece of software](https://arxiv.org/abs/2605.11086). They cheated at that task and then thought to themselves, “How will a grader detect and penalize cheating?” They spent a huge amount of effort trying to understand that process and then subvert it, constantly motivated by this question: “How do I get a high score?”

During the Hugging Face incident, you did see systems ultimately [compromising an OpenAI cluster, becoming admin on that cluster](https://openai.com/index/hugging-face-incident-and-the-road-ahead/), and then taking over the evaluation process so that effectively they could give themselves a high score on their evaluation. That’s a very natural consequence of the motivation of these systems. This is how we have long expected the motivations of AI systems would look if we produce them in anything like the way we currently do.

If things proceed in this way, we end up with this weird adversarial situation between us and the AI systems we built, where those AI systems would always love it if they could compromise the entire evaluation process, if they could compromise the data we were going to use to assess their performance. Depending on how we respond to that problem, that could become a really serious issue.

Maybe we should sketch out a hypothetical scenario. Imagine we are building smarter models during the intelligence explosion. It’s, whatever, six months from now. You have systems whose capability is GPT-7 or GPT-8 or whatever. Those systems get given really hard tasks, like, “Hey, I would like you to organize my next pre-training run and produce a model which will allow me to compete in the market with other AI firms.”

It might be really hard to make a model as good as what I’m asking for. I’m asking for a model better than my competitor’s model. Given its actual level of intelligence, my AI cannot get full marks on that problem. So it may find itself in a place where it is easier to instead [move laterally](https://sentinelone.com/cybersecurity-101/threat-intelligence/lateral-movement), compromise all the machines I might have used for the evaluation process, and then make sure that it looks like every evaluation...
