---
type: concept
title: Advantages And Disadvantages Of Deep Learning
summary: While deep learning offers key advantages such as automated feature engineering,
  high adaptability, and the ability to process unstructured data, it also has disadvantages
  like high computational costs, massive data requirements, lack of transparency,
  and long training times.
entities:
- Deep learning
- Machine learning
- Artificial neural networks
- Mohammad Mustafa Taye
department: ai_eng
sources:
- path: raw/papers/computers-12-00091.pdf
  loc: p.6
  hint: Advantages And Disadvantages Of Deep Learning
last_compiled: '2026-08-21'
---

## TL;DR

While deep learning offers key advantages such as automated feature engineering, high adaptability, and the ability to process unstructured data, it also has disadvantages like high computational costs, massive data requirements, lack of transparency, and long training times.

## Technical Specifications

Deep learning offers several key advantages over traditional machine learning. Unlike standard machine learning algorithms where biased feature selection can lead to inaccuracy in class distinction, deep learning can automate the learning of feature sets for several tasks. It allows learning and classification to be accomplished simultaneously, which cuts down on the amount of time needed for feature engineering. Additionally, deep learning has the potential to generate novel features from the limited existing training data, and its continuous training makes its architecture change-adaptive and capable of solving a variety of issues.

By using unsupervised learning approaches, deep learning can produce results for tasks that are dependable and actionable. Furthermore, while deep learning systems take more time to set up and are more difficult to implement initially, they require little human intervention afterward, unlike traditional machine learning which requires more continuous human engagement. Deep learning models utilize neural networks, are designed to handle massive volumes of unstructured data, and perform better the more they learn. During testing, deep learning algorithms run extremely quickly compared to some machine learning techniques.

Despite these advantages, deep learning has several major disadvantages, particularly regarding data and resource requirements. Due to its complex multi-layer structure, a deep learning system requires a large dataset to smooth out noise and generate accurate interpretations. Deep learning requires far more data than traditional machine learning, which may be utilized with as few as 1000 data points, whereas deep learning often only needs millions of data points. Because the entire training process depends on the constant flow of data, there is less room for improvement in the training process. Additionally, as more datasets become available, computational training becomes substantially more expensive, and deep learning systems necessitate significantly more robust hardware and resources, increasing the utilization of graphics processing units.

Deep learning also lacks transparency and interpretability, creating a black-box perception where understanding a deep learning result is challenging. Transparency in fault revision is lacking because there are no intermediary stages to support a particular fault's claims, meaning a whole algorithm must be updated to address the problem. Furthermore, due to the enormous number of parameters in deep learning algorithms, training typically takes a long time; deep learning model training can take longer than a week, compared to machine learning algorithms which take only seconds to a few hours. Finally, current training methods based on unsupervised pre-training and supervised fine-tuning are not suitable for online learning because global fine-tuning requires online learning training that results in a local minimum output.

## Provenance

`raw/papers/computers-12-00091.pdf` — p.6

## Cross-References

_(none)_
