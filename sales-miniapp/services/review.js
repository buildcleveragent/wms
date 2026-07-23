import { api, uploadFile } from '../utils/request'

export const reviewService = {
  list: (productId, params = {}) => api.saleMiniProductReviews(productId, params),
  createDraft: (payload) => api.createSaleMiniReviewDraft(payload),
  draft: (id) => api.saleMiniReviewDraft(id),
  updateDraft: (id, payload) => api.updateSaleMiniReviewDraft(id, payload),
  uploadImage: (id, filePath) => uploadFile({
    url: `/api/sale-mini/reviews/${id}/images/`,
    filePath,
  }),
  deleteImage: (reviewId, imageId) => api.deleteSaleMiniReviewImage(reviewId, imageId),
  submit: (id) => api.submitSaleMiniReview(id),
}
