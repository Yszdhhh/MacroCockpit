---
title: "Responding to objections to recursive self-improvement"
summary: "What the skeptics get right about superintelligence"
publisher: "Dwarkesh Patel · 深度访谈"
author_role: "AI前沿与深度访谈"
tickers: ["MSFT", "INTC"]
url: "https://www.dwarkesh.com/p/rsiobjections"
score: 63
---

# Responding to objections to recursive self-improvement

## 大佬背景导读
- **专栏来源**：Dwarkesh Patel · 深度访谈（AI前沿与深度访谈）
- **作者定位**：顶级技术播客与长文作者 Dwarkesh Patel 专栏，长期对话一线大模型实验室掌舵人，追踪 Scaling Law 与算力边界。
- **发布时间**：2026-11-25 05:45 (BJT)
- **涉及标的**：MSFT、INTC
- **原文链接**：[https://www.dwarkesh.com/p/rsiobjections](https://www.dwarkesh.com/p/rsiobjections)

## 中文核心要点
- **核心议题**：Dwarkesh Patel · 深度访谈（AI前沿与深度访谈）围绕「AI 算力与硬科技」展开深度剖析，文章标题为《Responding to objections to recursive self-improvement》。
- **市场映射与倾向**：情绪偏向「中性拆解」，重点映射标的/资产为 MSFT、INTC（归属赛道：AI 算力与硬科技）。
- **摘要提炼**：What the skeptics get right about superintelligence
- **行文脉络**：原文核心章节包括「(00:00:00) – Intro；(00:00:24) – Jim: “Do you have a real job?”；(00:02:41) – Cindy Wu: Is AI research a verifiable domain?」。

---

## 原文全文

### (00:00:00) – Intro

**Dwarkesh Patel** 00:00:00

Okay, so I recently released [this essay](https://www.dwarkesh.com/p/rsi) and [this video](https://www.youtube.com/watch?v=1NHPDzpfR6Y) about recursive self-improvement and why I expect superintelligence by 2030. A wild claim, and understandably, lots of people had objections. Many of them are actually quite interesting and compelling, and I want to go through them here. And by the way, I’m joined by [Max Farrens](https://x.com/maxwellfarrens), who is my General Manager, and we ended up discussing all of these ideas as we were writing them down. And in fact, we paired in a lot of the writing.

### (00:00:24) – Jim: “Do you have a real job?”

**Max Farrens** 00:00:25

I think a good place to start is with [a comment on the Substack version of the post](https://www.dwarkesh.com/p/rsi/comment/386792536) that comes from a user named Jim, who writes as The 1690 Project. “Dwarkesh, do you have a real job where you use this stuff” (meaning AI) “in and day out? I am guessing you do not, because I do, and this is not what I am seeing at all.” What do you think?

**Dwarkesh Patel** 00:00:47

Those are two different questions. Do I use these things day in, day out? Yes. Do I have a real job? Debatable. Don’t answer that. Funny that the guy’s like bragging about having a real job. He’s like, he’s like a gotcha on me. Like, I’ve been one of the main people who for the last year and a half has been arguing that the basic learning capabilities that these models lack do in fact impair their productivity. I think people who a naive understanding of what it means to automate a job just look at a system like GPT-6 and say, oh, why can’t we have AGI? It can automate all the white-collar work. I think this is totally wrong. The point I’m making in this essay is that are currently worse than humans at almost everything, right? IT is 1% of the economy, well-scoped coding tasks is a small fraction of IT. In some things, AIs are in fact getting to human level or beyond human level, where we have lots of verifiable proxies. And I would argue that just like math, AI research has a lot of verifiable proxies. And so the consequences of narrow progress in AI research or towards AIs that can do AI research leads to general improvements in AI capabilities. So if you look at a system like GPT-3 and you compare it to GPT-6, GPT-6 is also narrow intelligence, right? It can solve Millennium Prize problems, but it can’t be my video editor. So it can do basically everything way better than GPT-3. What I am imagining is that disproportionate progress in ML research, or AIs that are good at doing ML research, will compress as much progress as we had between GPT-3 and GPT-6 into a single year. And so the end result is within a year of having a system like GPT-7 or GPT-8 or whatever it ends up being, we end up with systems that are more generally capable in the way that humans are generally capable, and are on track to then rapidly become better than humans at everything.

### (00:02:41) – Cindy Wu: Is AI research a verifiable domain?

**Max Farrens** 00:02:42

So [Cindy Wu writes on X](https://x.com/cindyxywu/status/2121700522184851956), “Dwarkesh conveniently sweeps under the rug that climbing a metric involves defining it. Supposing you define it in a way that avoids Goodharting, reward hacking, etc., you need data and environments which do the lifting on defining good metrics. This is most of the game of making AGI, converting fuzzy concepts to formalized objects you can climb without gaming, which requires costly fixes later. He elides the hard problem by saying AI will just automate data.”

**Dwarkesh Patel** 00:03:12

I think there’s two parts to Cindy’s objection here. One is: can we find metrics which capture general AI progress? Are we aware of such metrics? And two, will we be able to generate the data that makes AIs good at getting better at those metrics? A big part of my essay was dedicated to articulating what those verifiable proxies for general AI progress are, right? So in pre-training, the loss on a held-out corpus is not only a proxy for pre-training progress, it is the basis of pre-training progress, and that is extremely verifiable. And there’s a lot of other proxies you can come up with that are just general intelligence capabilities that are kind of agnostic for any given paradigm. For example, how fast do you master a video game you’ve never seen before? This is one of the key ways that humans are different from current AIs, that humans master new video games really rapidly. How fast can an AI that has only seen a data corpus up to 1700 figure out general relativity? How many open math problems can it solve, right? So there’s a lot of paradigm-agnostic, hard metrics of progress we can come up with. The second argument here is learning algorithms. The application of learning algorithms is very general. The transformer was invented to beat the existing state of the art in translation, but because that architecture, or the implications of the architecture, were general, it also was really good at predicting the next token in WikiText. It’s also really good at generating images and so on. And similarly with reasoning models. Initially they were invented and they were debugged to, like, solve high school math problems and simple coding problems, and now they’re applicable and useful across lots of different domains. And so the breakthroughs that are made in AI end up being measurable on lots of different metrics. And the reason they’re measurable across lots of different metrics is because AI, or intelligence, is general, and because of that, its general improvements show up and make contact with reality in lots of different ways.

**Max Farrens** 00:05:14

So you started out by saying that there were two subpoints you wanted to address. The first is around verifiable proxies, and I think I understand that your answer is something of the sort of: it is not just that there are verifiable proxies that we can hill-climb, they are, like, the things themselves that we can work on. And so AI research makes itself particularly amenable to hill climbing. The second is around how we actually get the data to do that sort of work, and that is the part I don’t think I understand. So I’d be curious what your thoughts are.

**Dwarkesh Patel** 00:05:44

Yeah. So it is true that data is a big part of AI progress. The question is, what is the nature of those data improvements? And it seems to be the case, based on external evidence, that the nature of those improvements are the things that ML researchers who are not experts in any domain other than ML research are able to do. So when you look at how do pre-training data improvements happen, I actually did some research on this. We ended up spending probably on the order of, like, tens to maybe $100,000 of GPUs on this.

**Max Farrens** 00:06:15

I won’t say it here.

**Dwarkesh Patel** 00:06:18

Hopefully it was worth it just so I can say this one comment, which is, [we did this study](https://www.dwarkesh.com/p/pretraining-progress-is-mostly-data) about, if you look at data corpuses from... Well, this is Jerry Han, by the way, who’s a student at Princeton. And we did the study: if you look at data corpuses from 2019 up till 2026, and then you look at architectures from 2019 till 2026, which explains why we’ve gotten better at pre-training? And we found that actually data explains more of the progress than the architectures. But what we found is that if you look at how that data has gotten better, so that people have released public datasets of pre-training over time, the main improvements are just better methods for filtering data and better methods for coming up with synthetic data. And this is just work that ML researchers are doing on their computers, who are not experts in Shakespeare or quantum mechanics or whatever is in the pre-training corpus. They’re just doing empirical science about, hey, if I filter the data this way instead of that way, will my end model be better at solving math problems? If I make synthetic data this way instead of that way, will my end model be good at doing law or whatever? And so this is just, like, bread-and-butter ML research work.

**Max Farrens** 00:07:33

To jump in, just: you mean as opposed to having to hire a bunch of experts in law and then a bunch of experts in banking and a bunch of experts in... to generate a bunch of, like, very sort of specific examples, it is more that these ML researchers, who themselves may know nothing about these domains, can do it alone.

**Dwarkesh Patel** 00:07:51

Exactly. Now there is a question about: can you automate those ML researchers? And this is a question... I mean, I’ll explain my argument, the one I laid out in the blog post. One can disagree with this argument. The argument that I made is that yes, we do require some generalization. Not all parts of AI research are verifiable. And so to make the claim that we will get AI systems that are as competent as the top human researchers requires AIs to generalize. But I think there’s pretty compelling arguments that we will see this kind of generalization. If you look at math, for example, math as a domain may be, quote unquote, “verifiable”, but it’s not like we solve this Millennium Prize problem by training the AI to solve other Millennium Prize problems, right? There is generalization from more tractable and less ambitious problems to Millennium Prize problems. Or you look at coding agents today. Obviously there’s been tons and tons of training on different kinds of proxies for coding ability, but it’s not like the particular program you’re using Claude to write was one that was actually specifically trained for. It was not trained to help me make a Blood on the Clocktower environment, yet it generalized. And so there is some argument or assumption that there will be generalization based on a scaffold of tremendous amounts of verifiable proxies that are getting optimized, and then this will lift the boats that are... some weird analogy about a tide that is adjacent to the boats.

**Max Farrens** 00:09:15

And so it’s basically... it’s just like, look, if you believe that ML researchers can currently, sort of on their own, independently improve data in a way that continues progress, which it seems like you would argue there’s evidence to support, then if you have some other entity that is as capable as current human ML researchers at their job, there’s no reason to think they couldn’t also do that and continue to do that. And so then the crux is just like, okay, well, do you think we can get AI systems who are as competent at ML research as the current top in the field? And then there’s arguments for that.

**Dwarkesh Patel** 00:09:49

And another part of this argument is that as AI systems become better, they get better at learning. So if you wanted to make GPT-2 really good at being a lawyer, it would be almost impossible to fine-tune GPT-2 to make it like a Harvey agent, right? But GPT-6 is like a shitty intern lawyer. And GPT-8 will be the kind of entity where it’s like, very little data, just, it can go into the office, it can start interacting with clients. The way that a new... I know nothing about law, I don’t know why I picked this example. But I’ll pick something closer. Maybe a very competent person who might be a video editor on my phone.

**Max Farrens** 00:10:31

Sorry.

**Dwarkesh Patel** 00:10:33

What?

**Max Farrens** 00:10:36

Don’t... I will say, this doesn’t help you beat the allegations of knowing about any job other than video. Like, what do people do in the economy? Sort of like, 10% of GDP is video editing.

**Dwarkesh Patel** 00:10:51

But yeah, let’s go back to law. I mean, sorry, you don’t need to know anything about law to know that humans are able to learn to do law, right? At some point. So what I’m saying is that making really fast progress in general ML research will give us systems that can learn from less and less well-structured data.

### (00:11:10) – corsaren: The overarching objection

**Max Farrens** 00:11:10

I want to dive into [this tweet thread by corsaren](https://x.com/corsaren/status/2122574549707292976). And overall, it’s quite long. There are different individual objections that we should dive into, but I want to start with sort of like one overarching thing. He says, “Many of the intuitions and arguments driving Dwarkesh’s view in favor of near-term ASI” (which he says, for example, end of year 2027) “as a high probability outcome, are, in my opinion, quite bad.”

**Dwarkesh Patel** 00:11:37

It would be good to go through all the specific directions. I think overall, it was the best breakdown of the points of contention, the real crux in this debate. I don’t think that we will have superintelligence in 2027. The subtitle of my essay was, why we will have superintelligence by 2030. I say multiple points in the essay that my median year for fully automating the best AI researchers is 2028, and then superintelligence the year after, so something like 2029. And I think it’s plausible that we have superintelligence next year, like 25% chance or something, which is really big.

**Max Farrens** 00:12:11

It’s still crazy.

**Dwarkesh Patel** 00:12:13

But I think a part of the crux here might just be that he thinks there’s some knockdown argument that it happens in a few months, which I’m not arguing for.

### (00:12:20) – corsaren: Are the essay’s examples sloppy intuition pumps?

**Max Farrens** 00:12:20

Another sort of broader point from corsaren. Writes that in your essay, “about 50% of your examples are the sorts of sloppy intuition pumps and what-ifs that include a ton of detail but a noticeable lack of rigor.”

**Dwarkesh Patel** 00:12:36

He’s right. Moving on. No, I mean, have to rely on intuition pumps for a lot of this, because we’ve never built superintelligence before. We’ve never built human-level AI intelligence before. So we don’t... there’s no, like, mathematical proof you can write down, that you can formalize in Lean, that will tell you what is about to happen. You have to rely, ultimately, on analogies, and analogies that might or might not be valid. And then you have to debate out: is this a valid analogy? He’ll have objections to the analogies I present. I will present my rebuttals to those objections. Maybe I’ll come up with better analogies. Maybe I’ll discover that those analogies are in fact bad. But else are we supposed to do? You know, we have AI systems. Over the last five years, we’ve gone from AI systems that couldn’t hold down a conversation to ones that can now solve a Millennium Prize problem. Over the last 10 years, we had AI systems that couldn’t even label an image properly. And now we’re facing the prospect of another few years of progress at this rate. We don’t know what that means. We just have to, like, use intuitions about what that could mean based on what happened in history, what happened with evolution, and the other alternative is just to not face the possibility that progress in this arena is going to continue and is going to have big implications for the world.

### (00:13:51) – corsaren: Is online learning AI’s Millennium Prize problem?

**Max Farrens** 00:13:51

Another one. Quote, “Sample efficiency and online learning are AI’s equivalent of a Millennium Prize problem. And mathematics has already shown us what happens to such problems.” End quote. Then his response is, “Sure, but there’s a difference between ‘AI can solve a Millennium-level problem’ and ‘AI can solve this specific Millennium-level problem.’ If the blocker is a specific problem, the timing spread becomes much wider.”

**Dwarkesh Patel** 00:14:18

Yep, that makes sense. And in fact, that’s why I’m not saying AIs can solve Millennium Prize problems, therefore sample efficiency will be solved tomorrow. That being said, let me explain: I think it’s still a fair analogy. There’s a couple points to it. I think it would be fair to say, if I said, look, AI can solve protein folding, and protein folding used to be hard, so why can’t it make nanomachines? You know? It’s like, well, it’s like one particular problem that, you know, fit the kind of deep learning we’re doing in 2020. That doesn’t mean every single problem will get solved. But as I argue in my blog post, the particular kind of progress that AI is eliciting in mathematics, and not just in the Millennium Prize problem, that is just one example. There have been hundreds of open problems that have been solved. You know, of the problems that Paul Erdős compiled by the time he died, of these big 1,000 open problems in mathematics, more than 10% of them have been solved by AI by now. And it’s been, I don’t know, six months, a year, but maybe less, that we’ve had AI systems that are capable of doing frontier mathematics. So I made an object-level argument about why the kind of progress that would dramatically accelerate AI is analogous to what is happening in mathematics. For example, if you look at the invention of the transformer, I went through this argument that is based on something that Sholto talked about, about the pieces that make a transformer work better than an LSTM. You know, you don’t just need attention, but you need to combine attention with layer norm and residual connections and the Adam optimizer and positional encodings. Many of these pieces were in the literature for many different years, just the same way that people talk in mathematics about, like, oh, this idea, this technique was out there, and then we just need to combine it with some other technique, and then persistently try different kinds of combinations together until you come up with some empirical result that can be tested against reality. That is what historically ML research has consisted of. Now, it is possible that sample efficiency or continual learning is actually an intractable problem that is not like the kinds of ML progress we’ve seen in the past. We’ve seen AIs crack, for example, natural language understanding, we’ve seen them crack reasoning, we’ve seen them before that just crack basic understanding of objects in the world, right? Maybe sample efficiency and continual learning are not like that. Here’s why I don’t think that’s the case. We already see glimmers of continual learning and sample efficiency in current AI systems. So within the context window, AIs have arguably human-level sample efficiency. You know, you can, like, explain something to them when you’re having a conversation with them in chat, and they understand it about as well as a human would understand it. Now the question is, can you extend that under? You know, if you’re onboarding a human employee, they might be able to use the lessons they learned six months ago in this deep way where they can call upon their tacit knowledge. Whereas AI systems can only retain that kind of knowledge over the course of hours to days. That’s how long their context window is. And my argument is, if we already see the basis or the functionality that simply needs to be extended over time, I think that’s a pretty robust reason to think that extending the current trajectory of progress in ML would give you substantial progress in continual learning and sample efficiency.

### (00:17:39) – corsaren: Is human evolution evidence for transfer?

**Max Farrens** 00:17:40

So he’s quoting you again. Quote, “The other big piece of evidence for transfer is human evolution,” end quote. And then he says, “Except if AI was following the sort of pattern we saw for human evolution, we should have seen better transfer long before AI started solving Millennium problems. This spikiness has been one of the big surprises of AI.”

**Dwarkesh Patel** 00:18:00

I don’t think it’s the case that you can characterize current AIs as more spiky given their data distribution than humans are, or again, humans are relative to the evolutionary environment. Are really bad at adding 10-digit numbers in our head. Because that is not something that our evolution has incentivized our brain to get good at. Now, we’re extremely good at reading facial cues in a way that if you didn’t know ex ante we were able to do this... It’s just how much we can learn about a person by a few seconds of interaction. We have these crazy capabilities that are analogous to how good AI is at doing mathematics. My claim in the transfer argument is that evolution was trying to make us good at using facial cues to keep our standing in a tribe, being able to keep track of what’s happening around us, so we can figure out what animal to hunt and what animal’s trying to hunt us. And while we’re especially good at those things, there’s been enough transfer from that to things we were never trained for. For example, building semiconductors, coming up with new mathematics. Like, we’re shit at mathematics, but we’re good enough that we came up with, pretty good for a human, right? Like topology or whatever. I’m not a mathematician. And what I’m arguing is that with AIs, we’re starting with the mathematics. We’re starting with doing the Millennium Prize, and we’re hoping for transfer along the things that humans have been trained for, but also many things that humans have not been trained for. The humans were not trained to run multi-trillion-dollar corporations in the modern world that are based on advanced technology. Humans are good at that. And so I’m arguing that we’re starting from different... the trench that has been dug is different, and then we’re talking about, like, will both things overflow? And for the reasons I just listed out, I think there’s good reasons to think there will be overflow, both from the fact that we’re seeing current AI systems generalize from things they were not explicitly trained for. This is in a footnote, but maybe in the spirit of, like, articulating all my footnotes here. You people saw this [video I made about the Blood on the Clocktower benchmark](https://www.youtube.com/watch?v=1NQwfImTseU). There’s a social deception game. It’s based around, like, Mafia or Werewolf, where there’s a good team and evil team, good players don’t know who all the other evil players are, they’re to catch them. It requires a ton of long-horizon strategic thinking and detective work and considering how overlapping streams of evidence play together. And you look at the improvement AIs have had on this, it’s like Astra has a 90% win rate or more against models that were released at the beginning of the year. And this is what I’m arguing for, that we’re making AIs better at coding and math and whatever. And of course, that’s not just the only thing we’re doing, but we didn’t make them specifically good at this kind of create... it requires going into some of the plays we saw during this video. I encourage people to watch this Blood on the Clocktower video. And this is not the only piece of evidence, right? I’m not like, I did this evaluation and therefore there’s a transfer. We just generally talk to these models, they’re getting better across a broad range of domains. In fact, a big part of my argument is the distinction between evolution and what we’re doing with AIs is that, like, the scale of training, if you want to use that word, the scale of optimization that went into evolution is so much bigger than what we put into current AI systems. RL has been around for like a year and a half, like two years. And we probably scaled it up 100x, 1,000x over the last year. Even without RSI, there will be a 100x scale-up over the next two, three years. With RSI, there could be a much bigger scale-up. And so what I’m arguing is that we’ve currently done a very small fraction of the amount of scoring optimization that evolution did. We’re already seeing a lot of transfer from that, and that optimization is for skills that are especially irrelevant to t...
