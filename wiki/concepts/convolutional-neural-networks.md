---
type: concept
title: Convolutional Neural Networks
summary: Convolutional Neural Networks are highly prominent deep learning algorithms
  that automatically extract important features using shared weights and local connections,
  and they are widely applied in fields like computer vision, facial recognition,
  and natural language processing.
entities:
- Mohammad Mustafa Taye
- Philadelphia University
- Deep Learning
- Machine Learning
- Artificial Neural Networks
department: ai_eng
sources:
- path: raw/papers/computers-12-00091.pdf
  loc: p.12
  hint: Convolutional Neural Networks
last_compiled: '2026-08-21'
---

## TL;DR

Convolutional Neural Networks are highly prominent deep learning algorithms that automatically extract important features using shared weights and local connections, and they are widely applied in fields like computer vision, facial recognition, and natural language processing.

## Technical Specifications

Convolutional Neural Networks (CNNs) represent the most prominent and widely used algorithm in the field of deep learning. The primary advantage of a CNN over its predecessors is that it automatically picks out important parts without any help from a person. CNNs have been utilized widely in a variety of fields, such as computer vision, voice processing, face recognition, and natural language processing. Similar to a normal neural network, the structure of CNNs is influenced by neurons in human and animal brains, simulating the complicated sequence of cells that make up the visual cortex of a cat's brain. Goodfellow et al. identified three significant advantages of CNNs: comparable representations, sparse interactions, and parameter sharing.

In contrast to typical fully connected networks, a CNN employs shared weights and local connections to make full use of two-dimensional input data structures such as picture signals. This method uses an extremely small number of parameters, which makes training the network easier and speeds it up. This is similar to the cells of the visual cortex, where only tiny parts of a scene are perceived by these cells as opposed to the entire picture, spatially extracting the available local correlation in the input. A popular version of the CNN is similar to the multi-layer perceptron in that it has many convolution layers followed by subsampling or pooling levels and fully connected layers as the last layers.

The input x of each layer in a CNN model is structured in three dimensions: height, width, and depth, or m x m x r, where the height (m) equals the width (m). The term depth is also known as the channel number, which is equal to three in an RGB image. Multiple kernels available in each convolutional layer are designated by k and have three dimensions (n x n x q), where n must be less than m and q must be equal to or less than r. These kernels serve as the foundation for the local connections, which share comparable characteristics (bias bk and weight Wk) for producing k feature maps hk with a size of (m - n - 1) and are convolved with the input.

By adding nonlinearity or an activation function to the output of the convolution layer, the model obtains hk = f(Wk * x + bk). Then, each feature map in the subsampling layers is downsampled, resulting in a decrease in network parameters, which speeds up training and facilitates the resolution of the overfitting problem. The pooling function, such as maximum or average, is applied to a neighboring region of size p x p, where p is the kernel size, for all feature maps. The fully connected layers then receive the mid- and low-level data and generate the high-level abstraction, corresponding to the final stage layers of a normal neural network. Finally, classification scores are produced by the last layer, such as support vector machines (SVMs) or SoftMax, with each score reflecting the likelihood of a specific class for a particular event.

In terms of development, Kunihiko Fukushima presented the first CNN architecture that can identify visual patterns such as handwritten letters. Later, Yann LeCun used backpropagation to teach a CNN to detect handwritten numbers. In practice, adopting a convolutional neural network instead of standard iris sensors can be more successful for iris identification. Additionally, facial recognition represents a noteworthy use of deep learning in digital image processing. For example, Sighthound Inc. tested a deep convolutional neural network system that can identify emotions in addition to age and gender.

## Provenance

`raw/papers/computers-12-00091.pdf` — p.12

## Cross-References

_(none)_
