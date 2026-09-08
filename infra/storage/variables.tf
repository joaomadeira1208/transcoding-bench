variable "bucket_prefix" {
  description = "Primeiro segmento do nome dos dois buckets; o account id e o papel completam."
  type        = string
  default     = "transcoding-bench"
}
