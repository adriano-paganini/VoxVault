import base64

from encryption import decrypt_data, decrypt_symmetric_key


class Recording:
    def __init__ (self,timestamp:int,total_chunks:int,unprocessed_symmetric_key: str):
        self.timestamp = timestamp
        self.total_chunks = total_chunks
        self.unprocessed_symmetric_key = unprocessed_symmetric_key
        self.serialized_symmetric_keyset = decrypt_symmetric_key(unprocessed_symmetric_key)
        self.chunks = {}
        self.complete = b""

    def add_chunk(self, chunk_index:int, unprocessed_chunk_data:str):
        decoded_encrypted_data = base64.b64decode(unprocessed_chunk_data, validate=True)
        decrypted_pcm_bytes = decrypt_data(
            decoded_encrypted_data,
            self.serialized_symmetric_keyset,
        )
        self.chunks[chunk_index] = decrypted_pcm_bytes

        return decrypted_pcm_bytes

    def _is_complete(self):
        return len(self.chunks) == self.total_chunks

    def stitch(self):
        if self._is_complete():
            self.complete = b"".join(
                self.chunks[i] for i in range(self.total_chunks)
            )
            return True

        return False
