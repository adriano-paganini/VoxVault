package com.paganini.voxvault.service

import com.google.crypto.tink.HybridEncrypt
import com.google.crypto.tink.InsecureSecretKeyAccess
import com.google.crypto.tink.KeyTemplates
import com.google.crypto.tink.KeysetHandle
import com.google.crypto.tink.RegistryConfiguration
import com.google.crypto.tink.StreamingAead
import com.google.crypto.tink.TinkProtoKeysetFormat
import com.google.crypto.tink.hybrid.HpkeParameters
import com.google.crypto.tink.hybrid.HpkePublicKey
import com.google.crypto.tink.hybrid.HybridConfig
import com.google.crypto.tink.streamingaead.StreamingAeadConfig
import com.google.crypto.tink.subtle.Base64
import com.google.crypto.tink.util.Bytes
import com.paganini.voxvault.AppConfig
import java.io.File
import java.io.FileOutputStream
import java.io.OutputStream

class EncryptionService(
    outputFile: File?
) : AutoCloseable {

    val symmetricKey: KeysetHandle
    val encryptedOutputStream : OutputStream

    val publicKey : HybridEncrypt

    init {
        StreamingAeadConfig.register()
        HybridConfig.register()


        val publicKeyBytes = Base64.decode(AppConfig.Encryption.ENCRYPTION_PUBLIC_KEY,
            Base64.DEFAULT)

        publicKey = createHybridEncrypt(publicKeyBytes)

        symmetricKey = KeysetHandle.generateNew(
            KeyTemplates.get("AES128_GCM_HKDF_4KB")
        )

        val streamingAead = symmetricKey.getPrimitive(
            RegistryConfiguration.get(),
            StreamingAead::class.java
        )

        encryptedOutputStream = streamingAead.newEncryptingStream(
            FileOutputStream(outputFile),
            byteArrayOf()
        )


    }

    override fun close() {

    }

    fun getSerializedEncryptedSymmetricKey():String{
        val serializedKey = TinkProtoKeysetFormat.serializeKeyset(
            symmetricKey,
            InsecureSecretKeyAccess.get()
        )
        val encryptedSerializedKey = publicKey.encrypt(
            serializedKey,
            byteArrayOf()
        )
        return Base64.encodeToString(
            encryptedSerializedKey,
            Base64.NO_WRAP
        )
    }

    fun createHybridEncrypt(publicKeyBytes: ByteArray): HybridEncrypt {
        require(publicKeyBytes.size == 32)

        val parameters = HpkeParameters.builder()
            .setKemId(HpkeParameters.KemId.DHKEM_X25519_HKDF_SHA256)
            .setKdfId(HpkeParameters.KdfId.HKDF_SHA256)
            .setAeadId(HpkeParameters.AeadId.AES_256_GCM)
            .setVariant(HpkeParameters.Variant.NO_PREFIX)
            .build()

        val publicKey = HpkePublicKey.create(
            parameters,
            Bytes.copyFrom(publicKeyBytes),
            null
        )

        val handle = KeysetHandle.newBuilder()
            .addEntry(
                KeysetHandle.importKey(publicKey)
                    .withRandomId()
                    .makePrimary()
            )
            .build()

        return handle.getPrimitive(
            RegistryConfiguration.get(),
            HybridEncrypt::class.java
        )
    }

}