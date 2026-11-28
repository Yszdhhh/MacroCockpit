---
title: "The Trifecta"
summary: "I want to go Three for Three. I’m not talking about basketball, but my ability to dream up new derivatives or use cases and see them become financial industry standards."
publisher: "Arthur Hayes · 加密宏观"
author_role: "加密宏观与美元流动性"
tickers: ["META", "AMZN", "BTC"]
url: "https://cryptohayes.substack.com/p/the-trifecta"
score: 84
---

# The Trifecta

## 大佬背景导读
- **专栏来源**：Arthur Hayes · 加密宏观（加密宏观与美元流动性）
- **作者定位**：BitMEX 联合创始人 Arthur Hayes 长文专栏，核心围绕美元财政赤字、美债流动性投放与比特币周期定价。
- **发布时间**：2026-10-27 07:01 (BJT)
- **涉及标的**：META、AMZN、BTC
- **原文链接**：[https://cryptohayes.substack.com/p/the-trifecta](https://cryptohayes.substack.com/p/the-trifecta)

## 中文核心要点
- **核心议题**：Arthur Hayes · 加密宏观（加密宏观与美元流动性）围绕「加密宏观与链上周期」展开深度剖析，文章标题为《The Trifecta》。
- **市场映射与倾向**：情绪偏向「中性拆解」，重点映射标的/资产为 META、AMZN、BTC（归属赛道：加密宏观与链上周期）。
- **摘要提炼**：I want to go Three for Three. I’m not talking about basketball, but my ability to dream up new derivatives or use cases and see them become financial industry standards.

---

## 原文全文

[https://substackcdn.com/image/fetch/$s_!SxRe!,f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2F75936c7c-7601-4230-8bbf-6f955c2a8c45_400x560.jpeg](https://substackcdn.com/image/fetch/$s_!SxRe!,f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2F75936c7c-7601-4230-8bbf-6f955c2a8c45_400x560.jpeg)

(Any views expressed here are the personal views of the author and should not form the basis for making investment decisions, nor be construed as a recommendation or advice to engage in investment transactions.)

**Want More?** Follow the Author on **[Instagram](https://www.instagram.com/cryptohayes/)** , **[LinkedIn](https://www.linkedin.com/in/arthur-hayes-b493b42/)** and **[X](https://x.com/cryptohayes)**

Access the Korean language version here: **[Naver](https://blog.naver.com/maelstromkorea)**

Subscribe to see the latest Events: **[Calendar](https://lu.ma/mevents)**

Subscribe for new alerts

I want to go Three for Three. I’m not talking about basketball, but my ability to dream up new derivatives or use cases and see them become financial industry standards.

**My track record:**

**Perpetual Swap - Success (in a Borat accent)**

In May 2016, myself and my team at BitMEX invented the perpetual swap (“perps”). The perp became the most traded crypto financial product ever. And due to its popularity, it now powers RWA derivatives trading 24/7 on innovative platforms like Hyperliquid.[1]

**Synthetic Dollar Stablecoins - Success (in a Borat accent)**

In March 2023, I wrote an essay titled “**[Dust on Crust](https://cryptohayes.substack.com/p/dust-on-crust-300d4b5cf3ec)**”, where I outlined how to use long spot Bitcoin plus short perpetual swap to create a synthetic dollar. This synthetic dollar could then be packed into a stablecoin and used for payments throughout the crypto capital markets. The team at Ethena took my idea and executed perfectly. The Ethena synthetic dollar USDe became the fastest growing stablecoin ever. It went from $0 to over $15 billion in circulation in under eighteen months.

**The [Flop Network](https://flop.finance/) Spot Compute Market - TBD**

This summer, I unveiled my idea for the Flop Network. $FLOP is food for your AI agent. Agents will use $FLOP in commerce because $FLOP is directly convertible into compute, defined as floating point operations per second or FLOPS. If Flop becomes the payments network for AI agents, then it follows that the $FLOP/compute exchange rate will be the global benchmark for spot compute.

The natural offshoot of a truly global and liquid spot compute market is that derivatives can be created on top of this price. Let this essay be a definitive blueprint for any intrepid entrepreneur who wishes to create a compute derivatives market that is fit for purpose for the new AI agentic era.

**The Problem**

There is no global price for compute. There are various indices that attempt to track sale and rental prices for certain types of CPU, GPU, and TPU chips, but the data set is incomplete. In addition, there are various indices that attempt to track the price of AI tokens model by model. Both chip and token prices are derivatives of compute. If I know the price of compute (FLOPS), then I can derive the value of a chip and a model’s token. Instead of focusing on gathering imperfect information on chip and token prices, we should focus on obtaining information on the price of compute.

**The Solution: The Flop Network Compute Market**

Every second, the Flop Network produces a block. Inside each block are receipts that prove a miner completed an inference task for an agent. For each inference task, we know three things that help calculate the $FLOP/compute exchange rate.

Block information object:

- Latency in seconds - how long it took to complete the inference request
- Work done - total amount of floating-point operations completed
- Transaction fee - how much $FLOP is earned by the miner

$FLOP/(compute/second) = [3] / [2] / [1]

A spot price is not an actual price unless it is freely tradable by all. On the supply side, if you have a computer chip (GPU, CPU, or TPU), you can mine $FLOP by completing inference requests of agents. On the demand side, as long as you hold $FLOP in your wallet, you can submit an inference request. The Flop Network is an open-source network available to all. Therefore, the spot compute price becomes the global benchmark for compute.

**The Flop Compute Unit (FCU)**

Before I talk about cash settled derivatives, let’s continue on our journey in the “physical” compute market. One feature of the Flop Network is the ability to reserve compute from a miner to be delivered up to thirty days in the future. The network standardized FCUs to make them more tradable.

Imagine you know you will have a task to do in seven days’ time and you want to lock in the price of compute today. You can bid to reserve a certain amount of compute in seven days’ time from a miner. After seven days, the miner has twenty-four hours to deliver the compute. If accepted, the reservation creates a tradable seven-day FCU object which is akin to a deliverable forward. If the spot price of compute rises and you no longer need or want that compute, you can sell the FCU to another participant natively on the Flop Network.

Here is a concrete example of how a FCU works in practice. Please monitor changes to the official **[Flop docs](https://docs.flop.network/operators)**, which will contain all this information.

1 FCU object:

- Latency: 1 second
- Compute: 1 PetaFLOP
- AI Model: specified by purchaser
- Delivery date and time: 12:00 UTC in 7 days

**PLEASE NOTE:** FCUs cover inference, not training. Training capacity is worth more than the sum of its parts. Inference work is divisible, which is what makes a single reference unit or FCU possible.

For each AI model, there will be up to 30 tradable FCUs, one FCU for each day. If you need to reserve 1 ExaFLOP of compute, you would buy 1,000 FCU. As a buyer, in order to attract a miner, you post how much in $FLOP you would pay for that FCU. In order for the network to accept your bid, you must fully fund it. As a seller or provider of compute, you post how much $FLOP you want to receive for that FCU, you must post the stake weighted amount of $FLOP to act as a guarantee that if you fail to deliver another miner can profitably deliver the compute in the spot market. The stake weighted $FLOP amount is determined by the network based on recent total inference processed. It’s the cost in $FLOP and a cheating multiplier to deter dishonest mining and failure to deliver. This creates a fully funded two-sided orderbook devoid of spam. Funded bids and offers settle on chain, while the continuous two-sided orderbook and matches lives off-chain, exactly as with the spot market.

There is a failure to deliver risk. To mitigate that, as explained above, an FCU supplier must post a large $FLOP bond well in excess of the $FLOP fee received for inference from the buyer. A miner who fails to deliver the compute within twenty-four hours of expiry loses their entire $FLOP bond. Backup miners then bid to complete that order; they do so because they receive a much larger $FLOP fee per PetaFLOP than the prevailing spot price. If the spot price is greater than the $FLOP bond and no miner will deliver the compute, then there is a failure to deliver, and the buyer receives a full refund plus the $FLOP bond.

**The FRCR Futures Contract**

FRCR - Flop Reference Compute Rate

Hourly, volume-weighted $FLOP/GigaFLOPS print

I discussed how those who have real demand can, using $FLOP, buy compute on a spot or forward basis. Now let’s discuss how to structure a cash settled financial derivative that can trade on TradFi and or crypto exchanges. I will outline a proposed contract and discuss how a market maker can hedge themselves against long or short futures exposure.

Contract Specs:

Name: FRCR Quanto Quarterly Futures Contract

Expiry: 12:00 UTC Last Friday of March, June, September, and December

Underlying Index: FRCR

Multiplier: 1 USD per point

Example: FRCR = 100

Contract Value: Multiplier * FRCR = $1 * 100 = $100

Margin Currency: USD

If you go long one contract at an entry price of 100 $FLOP and it settles at 200 $FLOP, your PNL is the following:

Multiplier * Contracts * (Expiry Price - Entry Price) = $1 * 1 * (200 - 100) = $100

If you go short one contract at an entry price of 200 $FLOP and it settles at 100 $FLOP, your PNL is the following:

Multiplier * Contracts * (Expiry Price - Entry Price) = $1 * -1 * (100 - 200) = $100

A quanto derivative is an exotic derivative. The reason it must be a quanto derivative is that I assume that most participants want to post dollars as margin and earn dollars as profit, but the underlying spot market is denominated in $FLOP. Therefore, we must use the quanto structure to obtain the payoff that most market participants desire. To correctly price a quanto derivative, you must adjust by the covariance between the FRCR and the $FLOP/USD exchange rate.

If the market wanted to keep the margin currency in $FLOP, then:

Multiplier = 1 GigaFLOP

Contract Value = Multiplier * FRCR = 1 * 100 $FLOP = 100 $FLOP

The key to the success of any derivatives contract is how easy it is for market makers to reproduce the long and short futures contracts from their constituent parts.

**Long Futures Contract:**

To reproduce long exposure, the market maker must obtain $FLOP and then purchase a strip of FCUs. Here is how they synthetically create a 60-day long FRCR futures contract.

Spend $FLOP and buy a 30-day FCU. After 30-days roll that into another 30-day FCU.

On November 1, the 30-day FCU = 100 $FLOP. On November 30, you sell your original 30-day FCU for 100 $FLOP, and buy a new 30-day FCU for 200 $FLOP which expires on December 30.

You recreated a long 60-day futures contract, and your entry price was 200 $FLOP.

**Short Futures Contract:**

To reproduce short exposure, the market maker must obtain $FLOP and then supply a strip of FCUs. This will require more $FLOP as the bond is well in excess of the compute fee. The market maker doesn’t need to procure chips as they will roll the FCU before delivery. Here is how they synthetically create a 60-day short FRCR futures contract.

Spend $FLOP and supply a 30-day FCU. After 30-days roll that into another 30-day FCU supply contract.

On November 1, the 30-day FCU = 100 $FLOP. On November 30, you buy back your original 30-day FCU for 100 $FLOP, and supply a new 30-day FCU for 200 $FLOP, which expires on December 30. As long as you roll before the FCU expiry or find a backup miner during the 24-hour delivery window, you will not fail to deliver and lose your $FLOP bond.

You recreated a short 60-day futures contract, and your entry price was 200 $FLOP.

**The FRCR Perpetual Swap**

If we understand how the futures contract works, then extending it to a perpetual swap is quite simple. Remember that a perp is just an infinite strip of futures contracts.

Contract Specs:

Name: FRCR Quanto Perpetual Swap Contract

Underlying Premium Index: 8 Hour TWAP of [Perp Price - FRCR]

The Perp Price will be and hourly volume-weighted average to match the calculation methodology of the FRCR. Then there will be eight samples to average over eight hours.

Multiplier: 1 USD per point

Example: FRCR = 100

Contract Value: Multiplier * FRCR = $1 * 100 = $100

Margin Currency: USD

The funding mechanism is the same as that of traditional perps. If the premium index is positive, funding is positive and longs pay shorts. If the premium index is negative, funding is negative and shorts pay longs. As this is a quanto derivative, the payment is in USD.

To synthetically create a long perp, a trader would go long an FCU and continuously roll it. For example, if a trader wanted to create a synthetic long perp exposure for 120 days, they would buy a 30-day FCU and roll it four times. To synthetically create a short perp, a trader would supply an FCU and continuously roll. For example, if a trader wanted to create a synthetic short perp exposure for 120 days, they would supply a 30-day RCU and roll it four times.

**Which AI Model?**

One wrinkle for this market is that the compute price referenced by the derivatives is AI model agnostic. However, each model has a different FCU market. Therefore, to truly synthetically recreate the futures and perps, a trader would have to buy and supply a strip of FCUs for each model and in the correct weights to match the FRCR.

FRCR publishes per-model buckets alongside the composite, so the basket weights are observable. The composite drifts as the model mix changes, and model deprecation, not hardware obsolescence, becomes the risk a hedger manages.

**Frictionless**

In order to facilitate the growth of derivatives markets using the Flop Network derived spot and FCU compute prices, there will be no transaction fees to trade FCUs apart from $FLOP fees paid to validators to process FCU transactions between buyers and sellers. This fee does not differ from sending or receiving $FLOP from your wallet.

**The Benefits of Flop Spot and Forward Markets**

Anyone can participate in buying or supplying compute on the Flop Network.

The settled transactions execute on a public, transparent market.

All usage of Flop Network data is free.

It’s just that simple. If you want to build compute derivatives of any sort, you must start by observing activity and prices on the Flop Network’s spot and forward markets.

All hail the King!

**Want More?** Follow the Author on **[Instagram](https://www.instagram.com/cryptohayes/)** , **[LinkedIn](https://www.linkedin.com/in/arthur-hayes-b493b42/)** and **[X](https://x.com/cryptohayes)**

Access the Korean language version here: **[Naver](https://blog.naver.com/maelstromkorea)**

Subscribe to see the latest Events: **[Calendar](https://lu.ma/mevents)**

---

[1] RWA - Real World Asset (e.g. oil or gold)
